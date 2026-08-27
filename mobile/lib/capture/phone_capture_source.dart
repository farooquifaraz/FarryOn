import 'dart:async';
import 'dart:io' show File;

import 'package:camera/camera.dart';
import 'package:flutter/foundation.dart'; // compute() — off-main-isolate work
import 'package:flutter/services.dart';
import 'package:image/image.dart' as img;
import 'package:record/record.dart';

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
/// **Audio stack — `record` in, `flutter_sound` out.** Capture streams raw
/// **PCM16** at 16 kHz from `record` (background-thread capture — see the
/// note on [_recorder] for why flutter_sound's recorder had to go), and
/// playback streams PCM16 at 24 kHz through flutter_sound's player
/// (`PcmPlayer`). Together that is the exact 16 kHz-in / 24 kHz-out,
/// low-latency pipeline the protocol needs.
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
  //
  // `record`, NOT flutter_sound. flutter_sound's Android recorder pumped its
  // capture through a main-thread native loop that burned a full CPU core for
  // as long as the mic ran — profiled on-device 2026-08-27: main thread at
  // ~105% with the mic on, ~11% with it revoked, Dart itself ~2% busy. That
  // one loop was the app's ANRs, the choppy TTS, and the heat. `record`
  // captures on a background thread and hands Dart sane-sized PCM chunks.
  // flutter_sound remains the PLAYBACK engine (see playback/pcm_player.dart);
  // only capture moved.
  final AudioRecorder _recorder = AudioRecorder();
  final _audioController = StreamController<Uint8List>.broadcast();
  StreamSubscription<Uint8List>? _recorderSub;
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
  }

  // ---- Audio -------------------------------------------------------------

  @override
  Future<void> startAudio() async {
    if (_audioRunning) return;

    // `record` streams raw PCM16 chunks from a background capture thread —
    // no main-thread involvement (the whole point of the migration; see the
    // field note on [_recorder]).
    final stream = await _recorder.startStream(const RecordConfig(
      encoder: AudioEncoder.pcm16bits,
      sampleRate: AudioFormat.micSampleRate, // 16 kHz
      numChannels: AudioFormat.channels,
      // Hardware echo cancellation stays — without it the assistant's own
      // speaker audio fed back into the turn detector (phantom turns,
      // device-confirmed 2026-08-05). Noise suppression is OFF and auto-gain
      // ON since 2026-08-27: with suppression on, Samsung's voice pipeline
      // crushed real speech — audio streamed at full rate for minutes while
      // Gemini's VAD never opened a single turn, and what did transcribe
      // came out garbled ("तू अपनी गाड़ी जा" for none of those words).
      // Auto-gain restores the levels the mic gate's bar was tuned against.
      echoCancel: true,
      noiseSuppress: false,
      autoGain: true,
      androidConfig: AndroidRecordConfig(
        audioSource: AndroidAudioSource.voiceCommunication,
        // The session's audio focus/mode is managed by VoiceAudioMode and the
        // glasses bridge — the recorder must not fight them for it.
        audioManagerMode: AudioManagerMode.modeNormal,
        manageBluetooth: false,
      ),
    ));
    _recorderSub = stream.listen((chunk) {
      if (chunk.isNotEmpty) _audioController.add(chunk);
    });
    _audioRunning = true;
    _log.info('audio capture started @ ${AudioFormat.micSampleRate}Hz');
  }

  @override
  Future<void> stopAudio() async {
    if (!_audioRunning) return;
    _audioRunning = false;
    try {
      await _recorder.stop();
    } catch (e) {
      _log.warn('stopRecorder error: $e');
    }
    await _recorderSub?.cancel();
    _recorderSub = null;
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
  /// [initialize] (or [startVideo]) has run. While a phone video recording
  /// runs, this is the RECORDER's controller — the user must see exactly
  /// what is being recorded (user-asked 2026-08-27).
  CameraController? get cameraController => _videoRecorder ?? _camera;

  // ---- Video RECORDING (phone-camera fallback for record_video) ----------
  //
  // The streaming controller is created with `enableAudio: false` (the live
  // mic belongs to the assistant), so a recording swaps in a dedicated
  // audio-enabled controller for its duration. The caller must stop the
  // assistant's own mic capture FIRST — two owners of the microphone is how
  // a video ends up silent.

  CameraController? _videoRecorder;

  /// Whether a phone video recording is currently running.
  bool get isRecordingVideo => _videoRecorder != null;

  /// Start recording video+audio with the phone camera. Throws on failure so
  /// the caller can report the precise reason instead of pretending.
  Future<void> startVideoRecording() async {
    if (_videoRecorder != null) throw StateError('already recording');
    // The streaming pipeline and the recorder cannot share the camera.
    await stopVideo();
    await releaseCamera();
    final cameras = await availableCameras();
    final selected = cameras.firstWhere(
      (c) => c.lensDirection == _facing,
      orElse: () => cameras.first,
    );
    final rec = CameraController(
      selected,
      ResolutionPreset.high,
      enableAudio: true,
    );
    await rec.initialize();
    try {
      await rec.lockCaptureOrientation(DeviceOrientation.portraitUp);
    } catch (e) {
      _log.warn('recording lockCaptureOrientation failed: $e');
    }
    await rec.startVideoRecording();
    _videoRecorder = rec;
    _log.info('phone video recording started (${selected.name})');
  }

  /// Stop the phone recording and return the temp file path (null if none
  /// was running or the stop failed). Always releases the recorder.
  Future<String?> stopVideoRecording() async {
    final rec = _videoRecorder;
    if (rec == null) return null;
    _videoRecorder = null;
    try {
      final file = await rec.stopVideoRecording();
      _log.info('phone video recording stopped → ${file.path}');
      return file.path;
    } catch (e) {
      _log.warn('stopVideoRecording failed: $e');
      return null;
    } finally {
      try {
        await rec.dispose();
      } catch (_) {}
    }
  }

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
    await _recorder.dispose();
    await _camera?.dispose();
    _camera = null;
    await _audioController.close();
    await _videoController.close();
  }
}
