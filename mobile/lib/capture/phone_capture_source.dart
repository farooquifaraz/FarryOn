import 'dart:async';
import 'dart:io' show File;

import 'package:camera/camera.dart';
import 'package:flutter/foundation.dart'; // compute() — off-main-isolate work
import 'package:flutter/services.dart';
import 'package:flutter_sound/flutter_sound.dart';
import 'package:image/image.dart' as img;

import '../core/logger.dart';
import '../protocol/messages.dart';
import '../protocol/protocol.dart';
import 'capture_source.dart';

/// CHANGED (UX Spec BUG 3 / latency): inputs for the off-main-isolate JPEG
/// downscale. A plain record/class is required because `compute` can only hand a
/// single, sendable argument to the isolate entry point.
class _JpegJob {
  const _JpegJob(this.source, this.maxDim, this.quality);
  final Uint8List source;
  final int maxDim;
  final int quality;
}

/// Whether this JPEG is already small enough to send as it is.
///
/// Reads only the frame header — `startDecode` walks to the SOF marker for the
/// dimensions and stops, so this costs microseconds against the tens of
/// milliseconds a full decode takes.
///
/// It exists because the answer is almost always yes and we were not asking.
/// The camera runs at [ResolutionPreset.medium], which on every device tested
/// is at or under [VideoFormat.maxWidth] — so the "downscale" decoded and
/// re-encoded a whole frame, once a second, for a resize that never happened.
/// Passing the camera's own JPEG through skips the decode, the re-encode, the
/// isolate spawn and two full-frame copies. Same picture, same wire format.
///
/// Returns false when the header cannot be read, which routes the frame down
/// the old path rather than trusting bytes we could not measure.
@visibleForTesting
bool jpegFitsWithin(Uint8List jpeg, int maxDim) {
  try {
    if (!_looksComplete(jpeg)) return false;
    final info = img.JpegDecoder().startDecode(jpeg);
    if (info == null || info.width == 0 || info.height == 0) return false;
    final longEdge = info.width > info.height ? info.width : info.height;
    return longEdge <= maxDim;
  } catch (_) {
    return false;
  }
}

/// Whether these bytes are a whole JPEG, not the start of one.
///
/// Start-of-image at the front, end-of-image at the back. A file that was read
/// while it was still being written has a perfectly good header and no ending,
/// which is precisely the case that has to be caught here: the header is all
/// [jpegFitsWithin] reads.
///
/// The full decode used to do this by accident — a truncated frame decoded to
/// null and was dropped. Passing the camera's own bytes through to skip that
/// decode lost the accident with it, and a half-written frame went to the model
/// AND to the chat preview, where Android's decoder gave up with
/// "Input contained an error" (vivo V2246, 2026-08-19).
///
/// Cheap on purpose: two bytes at each end, no scan. Anything that fails goes
/// down the decode path, which either repairs it or drops it — the same
/// treatment it had before.
bool _looksComplete(Uint8List jpeg) {
  if (jpeg.length < 4) return false;
  final soi = jpeg[0] == 0xFF && jpeg[1] == 0xD8;
  final eoi = jpeg[jpeg.length - 2] == 0xFF && jpeg[jpeg.length - 1] == 0xD9;
  return soi && eoi;
}

/// Top-level (isolate-safe) JPEG decode + downscale + re-encode.
///
/// Runs on a background isolate via `compute` so the heavy `img.decodeImage`
/// (which previously ran on the UI isolate every ~1s and stalled audio
/// forwarding + WS sends — the "voice slow" symptom) no longer blocks the event
/// loop. Returns null if decoding fails.
///
/// Only reached now when the frame really is oversized — see [jpegFitsWithin].
Uint8List? _downscaleJpegInIsolate(_JpegJob job) {
  final decoded = img.decodeImage(job.source);
  if (decoded == null) return null;

  final longEdge =
      decoded.width > decoded.height ? decoded.width : decoded.height;

  final img.Image sized = longEdge > job.maxDim
      ? img.copyResize(
          decoded,
          width: decoded.width >= decoded.height ? job.maxDim : null,
          height: decoded.height > decoded.width ? job.maxDim : null,
        )
      : decoded;

  return Uint8List.fromList(img.encodeJpg(sized, quality: job.quality));
}

/// Phone implementation of [CaptureSource] using the device camera and mic.
///
/// **Audio stack — `flutter_sound`.** It is the most direct fit for this
/// contract: its recorder can stream raw **PCM16** straight to a Dart `Sink`
/// (`startRecorder(toStream:, codec: pcm16, sampleRate: 16000, numChannels: 1)`)
/// without any file/round-trip, and the matching player streams PCM16 back for
/// playback (used by `PcmPlayer`). That gives us the exact 16 kHz-in /
/// 24 kHz-out, low-latency, single-dependency pipeline the protocol needs.
/// (`mic_stream` + a separate player was the alternative, but it would mean two
/// audio dependencies and manual Int16 framing.)
///
/// **Video.** `camera` does not expose JPEG stills cheaply via its image
/// stream (that path yields YUV/BGRA planes). We instead throttle
/// `takePicture()` to ~1 fps, then downscale to ≤ [VideoFormat.maxWidth] and
/// re-encode to JPEG with the `image` package, matching INPUT_VIDEO (0x02).
class PhoneCaptureSource implements CaptureSource {
  PhoneCaptureSource({
    this.deviceId = 'phone-default',
    CameraLensDirection preferredCamera = CameraLensDirection.back,
    this.jpegQuality = 88,
  }) : _facing = preferredCamera;

  static final _log = Logger('PhoneCapture');

  final String deviceId;
  /// Active lens. Mutable so the user can flip between back and front
  /// (see [setFrontCamera]); the camera is reopened on the new lens.
  CameraLensDirection _facing;
  final int jpegQuality;

  // --- Audio ---
  final FlutterSoundRecorder _recorder = FlutterSoundRecorder();
  final _audioController = StreamController<Uint8List>.broadcast();
  StreamController<Uint8List>? _recorderSink; // raw PCM from flutter_sound
  StreamSubscription<Uint8List>? _recorderSub;
  bool _recorderOpen = false;
  bool _audioRunning = false;

  // --- Video ---
  CameraController? _camera;
  final _videoController = StreamController<Uint8List>.broadcast();
  Timer? _frameTimer;
  bool _capturingFrame = false;

  /// Interval between captured frames (~1 fps per [VideoFormat.fps]).
  Duration get _frameInterval =>
      Duration(milliseconds: (1000 / VideoFormat.fps).round());

  @override
  CaptureCapabilities get capabilities =>
      const CaptureCapabilities(audioIn: true, videoIn: true);

  @override
  DeviceInfo get info => DeviceInfo(
        kind: 'phone',
        id: deviceId,
        capabilities: capabilities.toWireCapabilities(),
      );

  @override
  Stream<Uint8List> get audio16k => _audioController.stream;

  @override
  Stream<Uint8List> get jpegFrames => _videoController.stream;

  @override
  Future<void> initialize() async {
    // The microphone only. The camera device is NOT opened here.
    //
    // Turning the camera off by default stopped the frames but not this: the
    // hardware was still claimed the moment a session started, for a feature
    // that was switched off. It shows on screen as "Camera off" while the
    // camera light is on, it costs battery, it blocks other apps, and it is
    // the path the app was on when the surface went black on both an S23 and
    // a vivo (2026-08-16 — `Camera: open | onDisconnected` and a dead-thread
    // handler, zero frames ever captured).
    //
    // [startVideo] opens it, and that is reached from [grabFrame] when the
    // scan button or `identify_image` actually needs a picture. Nothing that
    // wants the camera goes without it; nothing that doesn't want it pays.
    await _openRecorder();
  }

  // ---- Audio -------------------------------------------------------------

  Future<void> _openRecorder() async {
    if (_recorderOpen) return;
    await _recorder.openRecorder();
    _recorderOpen = true;
    _log.debug('recorder opened');
  }

  @override
  Future<void> startAudio() async {
    if (_audioRunning) return;
    await _openRecorder();

    // flutter_sound streams raw PCM into a sink we own; forward each chunk to
    // the public broadcast stream. Chunks land ~every codec buffer; at 16 kHz
    // mono these are comfortably inside the 20–100 ms guidance.
    final sink = StreamController<Uint8List>();
    _recorderSink = sink;
    _recorderSub = sink.stream.listen((chunk) {
      if (chunk.isNotEmpty) _audioController.add(chunk);
    });

    await _recorder.startRecorder(
      toStream: sink.sink,
      codec: Codec.pcm16,
      numChannels: AudioFormat.channels,
      sampleRate: AudioFormat.micSampleRate, // 16 kHz
      // Hardware echo cancellation + noise suppression, per platform:
      // - Android: `enableVoiceProcessing` is a NO-OP — the lever is the
      //   VOICE_COMMUNICATION audio source (below). Without it the default
      //   MIC source fed the assistant's own speaker audio back into the
      //   turn detector, and the AI answered itself/room noise (phantom
      //   turns, device-confirmed 2026-08-05).
      // - iOS: `enableVoiceProcessing` engages VoiceProcessingIO.
      audioSource: AudioSource.voice_communication,
      enableVoiceProcessing: true,
    );
    _audioRunning = true;
    _log.info('audio capture started @ ${AudioFormat.micSampleRate}Hz');
  }

  @override
  Future<void> stopAudio() async {
    if (!_audioRunning) return;
    _audioRunning = false;
    try {
      await _recorder.stopRecorder();
    } catch (e) {
      _log.warn('stopRecorder error: $e');
    }
    await _recorderSub?.cancel();
    await _recorderSink?.close();
    _recorderSub = null;
    _recorderSink = null;
    _log.info('audio capture stopped');
  }

  // ---- Video -------------------------------------------------------------

  Future<void> _openCamera() async {
    if (_camera != null) return;
    final cameras = await availableCameras();
    if (cameras.isEmpty) {
      _log.warn('no cameras available');
      return;
    }
    final selected = cameras.firstWhere(
      (c) => c.lensDirection == _facing,
      orElse: () => cameras.first,
    );
    final controller = CameraController(
      selected,
      // CHANGED (UX Spec BUG 3 / latency): high -> medium. We throttle to ~1 fps
      // and then downscale to <=VideoFormat.maxWidth anyway, so a full high-res
      // capture was wasted work that made each takePicture() (and its decode)
      // heavier and competed with the audio path. Medium is still sharp enough
      // for the vision model after downscaling, and noticeably lighter/faster.
      ResolutionPreset.medium,
      enableAudio: false, // audio comes from flutter_sound, not the camera
      imageFormatGroup: ImageFormatGroup.jpeg,
    );
    await controller.initialize();
    // Default to an upright portrait preview/capture (phones are held this way);
    // the user can switch to landscape via [setPortrait].
    try {
      await controller.lockCaptureOrientation(DeviceOrientation.portraitUp);
    } catch (e) {
      _log.warn('lockCaptureOrientation failed: $e');
    }
    _camera = controller;
    _log.debug('camera initialized: ${selected.name}');
  }

  /// Exposes the controller so the UI can render a live preview. Null until
  /// [initialize] (or [startVideo]) has run.
  CameraController? get cameraController => _camera;

  @override
  Future<void> startVideo() async {
    await _openCamera();
    if (_camera == null) return;
    _frameTimer?.cancel();
    _frameTimer = Timer.periodic(_frameInterval, (_) => _captureFrame());
    _log.info('video capture started @ ${VideoFormat.fps}fps');
  }

  @override
  Future<void> stopVideo() async {
    _frameTimer?.cancel();
    _frameTimer = null;
    _log.info('video capture stopped');
  }

  @override
  Future<void> captureOnce() async {
    // Already streaming means the user asked for a live view — take the frame
    // and leave their camera alone.
    final wasStreaming = _frameTimer != null;
    await _openCamera();
    if (_camera == null) {
      _log.warn('captureOnce: no camera');
      return;
    }
    try {
      await _captureFrame();
    } finally {
      // Closed again, not just paused. The camera light going out is the
      // user-visible promise that nothing more is being taken, and holding the
      // device open would keep it from other apps for no reason.
      if (!wasStreaming) await releaseCamera();
    }
    _log.info('captureOnce: one frame${wasStreaming ? '' : ', camera closed'}');
  }

  @override
  Future<void> releaseCamera() async {
    _frameTimer?.cancel();
    _frameTimer = null;
    try {
      await _camera?.dispose();
    } catch (e) {
      _log.warn('camera dispose failed: $e');
    }
    _camera = null;
    // Clear a possibly-wedged in-flight capture flag (a takePicture awaiting a
    // now-disposed controller may never resolve its finally) so the camera
    // streams cleanly after the next startVideo.
    _capturingFrame = false;
    _log.info('camera released (will reopen on next startVideo)');
  }

  @override
  Future<void> setPortrait(bool portrait) async {
    final camera = _camera;
    if (camera == null || !camera.value.isInitialized) return;
    try {
      await camera.lockCaptureOrientation(
        portrait
            ? DeviceOrientation.portraitUp
            : DeviceOrientation.landscapeLeft,
      );
      _log.info('orientation → ${portrait ? "portrait" : "landscape"}');
    } catch (e) {
      _log.warn('setPortrait failed: $e');
    }
  }

  @override
  Future<void> setFrontCamera(bool front) async {
    final target =
        front ? CameraLensDirection.front : CameraLensDirection.back;
    if (target == _facing) return;
    _facing = target;
    // Reopen the camera on the new lens. If frames were streaming, tear the
    // controller down and rebuild so the next capture uses the new lens, then
    // resume the ~1 fps timer.
    final wasStreaming = _frameTimer != null;
    _frameTimer?.cancel();
    _frameTimer = null;
    try {
      await _camera?.dispose();
    } catch (e) {
      _log.warn('camera dispose (flip) failed: $e');
    }
    _camera = null;
    // Critical: a `takePicture()` that was in flight when we disposed the old
    // controller may never resolve, so its `finally` never clears this flag —
    // leaving the capture loop wedged (every `_captureFrame` early-returns and
    // no frames reach the model). Reset it here before the new lens streams.
    _capturingFrame = false;
    await _openCamera();
    if (wasStreaming && _camera != null) {
      _frameTimer = Timer.periodic(_frameInterval, (_) => _captureFrame());
    }
    _log.info('lens → ${front ? "front" : "back"}');
  }

  @override
  Future<double> setZoom(double level) async {
    final camera = _camera;
    if (camera == null || !camera.value.isInitialized) return 1.0;
    try {
      final maxZoom = await camera.getMaxZoomLevel();
      final minZoom = await camera.getMinZoomLevel();
      final clamped = level.clamp(minZoom, maxZoom);
      await camera.setZoomLevel(clamped);
      _log.info('zoom → ${clamped.toStringAsFixed(1)}x (max $maxZoom)');
      return clamped;
    } catch (e) {
      _log.warn('setZoom failed: $e');
      return 1.0;
    }
  }

  /// Remove the file [takePicture] left behind, quietly.
  ///
  /// Separate so the capture path stays readable, and never throws: a frame
  /// that could not be deleted is a wasted file, not a reason to drop the
  /// picture we already read out of it.
  Future<void> _discard(XFile shot) async {
    try {
      await File(shot.path).delete();
    } catch (e) {
      _log.debug('could not delete captured frame: $e');
    }
  }

  Future<void> _captureFrame() async {
    final camera = _camera;
    if (camera == null || !camera.value.isInitialized) return;
    if (_capturingFrame) return; // skip if the previous shot is still in flight
    _capturingFrame = true;
    try {
      final shot = await camera.takePicture();
      final raw = await shot.readAsBytes();
      // The file `takePicture()` wrote is dead the moment we have the bytes,
      // and nothing else was deleting it. At one frame a second that is 3,600
      // JPEGs an hour left in the cache directory — and the phone slows down
      // as they pile up. On a vivo V2246 a ten-minute session decayed from 31
      // frames a minute to 1, and the UI thread stalled for seven and eight
      // seconds at a stretch, which shows on screen as an app that has died
      // (device-seen 2026-08-15). Best-effort: a frame is not worth failing
      // over, and the bytes are already in hand.
      unawaited(_discard(shot));

      // CHANGED (UX Spec BUG 3 / latency): downscale on a background isolate so
      // the per-second JPEG decode never blocks the UI isolate that also
      // forwards mic audio + WS frames. Only when there is something to
      // downscale, though — at [ResolutionPreset.medium] the camera's own JPEG
      // is already within [VideoFormat.maxWidth], so the common case now skips
      // the decode, the re-encode and the isolate entirely.
      final jpeg = jpegFitsWithin(raw, VideoFormat.maxWidth)
          ? raw
          : await compute(
              _downscaleJpegInIsolate,
              _JpegJob(raw, VideoFormat.maxWidth, jpegQuality),
            );
      if (jpeg != null && !_videoController.isClosed) {
        _videoController.add(jpeg);
      }
    } catch (e) {
      _log.warn('frame capture failed: $e');
    } finally {
      _capturingFrame = false;
    }
  }

  // ---- Lifecycle ---------------------------------------------------------

  @override
  Future<void> dispose() async {
    await stopVideo();
    await stopAudio();
    if (_recorderOpen) {
      await _recorder.closeRecorder();
      _recorderOpen = false;
    }
    await _camera?.dispose();
    _camera = null;
    await _audioController.close();
    await _videoController.close();
  }
}
