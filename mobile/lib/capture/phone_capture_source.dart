import 'dart:async';

import 'package:camera/camera.dart';
import 'package:flutter/services.dart';
import 'package:flutter_sound/flutter_sound.dart';
import 'package:image/image.dart' as img;

import '../core/logger.dart';
import '../protocol/messages.dart';
import '../protocol/protocol.dart';
import 'capture_source.dart';

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
    this.preferredCamera = CameraLensDirection.back,
    this.jpegQuality = 88,
  });

  static final _log = Logger('PhoneCapture');

  final String deviceId;
  final CameraLensDirection preferredCamera;
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
    await _openRecorder();
    await _openCamera();
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
      // OS-level voice processing (acoustic echo cancellation + noise
      // suppression) so the mic doesn't pick up the assistant's own TTS —
      // essential for the hands-free, always-listening experience.
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
      (c) => c.lensDirection == preferredCamera,
      orElse: () => cameras.first,
    );
    final controller = CameraController(
      selected,
      // High resolution so frames (and zoomed-in crops) stay sharp enough for
      // the vision model to read distant/small subjects accurately. We still
      // throttle to ~1 fps and downscale, so the cost stays modest.
      ResolutionPreset.high,
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

  Future<void> _captureFrame() async {
    final camera = _camera;
    if (camera == null || !camera.value.isInitialized) return;
    if (_capturingFrame) return; // skip if the previous shot is still in flight
    _capturingFrame = true;
    try {
      final shot = await camera.takePicture();
      final raw = await shot.readAsBytes();
      final jpeg = _downscaleToJpeg(raw);
      if (jpeg != null && !_videoController.isClosed) {
        _videoController.add(jpeg);
      }
    } catch (e) {
      _log.warn('frame capture failed: $e');
    } finally {
      _capturingFrame = false;
    }
  }

  /// Decode, downscale to ≤ [VideoFormat.maxWidth] on the long edge, and
  /// re-encode as JPEG. Returns null if decoding fails.
  Uint8List? _downscaleToJpeg(Uint8List source) {
    final decoded = img.decodeImage(source);
    if (decoded == null) return null;

    const maxDim = VideoFormat.maxWidth;
    final longEdge = decoded.width > decoded.height
        ? decoded.width
        : decoded.height;

    final img.Image sized = longEdge > maxDim
        ? img.copyResize(
            decoded,
            width: decoded.width >= decoded.height ? maxDim : null,
            height: decoded.height > decoded.width ? maxDim : null,
          )
        : decoded;

    return Uint8List.fromList(img.encodeJpg(sized, quality: jpegQuality));
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
