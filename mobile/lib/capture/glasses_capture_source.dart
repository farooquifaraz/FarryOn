import 'dart:async';
import 'dart:typed_data';

import '../core/logger.dart';
import '../protocol/messages.dart';
import 'capture_source.dart';

/// Smart-glasses implementation of [CaptureSource] — **transport stub**.
///
/// This class exists today to prove and document the adapter seam: because the
/// whole app talks to [CaptureSource] and nothing else, dropping in real glasses
/// means filling in the transport here, with **zero** changes to the data,
/// state, or UI layers.
///
/// It compiles and behaves safely (its streams simply stay silent until a
/// transport is wired), so it can be selected in the device registry for
/// development without crashing.
///
/// ---------------------------------------------------------------------------
/// TODO(transport): wire a real glasses transport. Likely shapes:
///   * **BLE** (GATT): subscribe to an audio characteristic that streams Opus or
///     PCM; transcode/resample to PCM16 mono 16 kHz before emitting on
///     [audio16k]. Video over BLE is usually impractical — many glasses send
///     periodic JPEG stills over a side channel.
///   * **Wi-Fi / RTSP / WebRTC**: pull an A/V stream from the glasses' on-device
///     server; decode audio → resample to 16 kHz → [audio16k]; grab key frames
///     ~1 fps → downscale ≤ 1024 px → JPEG → [jpegFrames].
///   * **Companion SDK**: many vendors ship a Flutter/native SDK; adapt its
///     callbacks onto the two streams below.
///
/// In all cases the *only* job is to normalize device output into the
/// [CaptureSource] contract: PCM16 LE mono 16 kHz audio chunks and ≤1024 px
/// JPEG frames. The rest of FarryOn is already device-agnostic.
/// ---------------------------------------------------------------------------
class GlassesCaptureSource implements CaptureSource {
  GlassesCaptureSource({
    this.deviceId = 'glasses-stub',
    this.endpoint,
  });

  static final _log = Logger('GlassesCapture');

  /// Stable identifier reported in `hello.device.id`.
  final String deviceId;

  /// Transport address for the future implementation (BLE id, RTSP URL, …).
  /// Currently informational only.
  final String? endpoint;

  final _audioController = StreamController<Uint8List>.broadcast();
  final _videoController = StreamController<Uint8List>.broadcast();

  bool _audioRunning = false;
  bool _videoRunning = false;

  @override
  CaptureCapabilities get capabilities =>
      // Most glasses provide mic + camera; many also have a speaker, but
      // playback routing is out of scope for the stub.
      const CaptureCapabilities(audioIn: true, videoIn: true);

  @override
  DeviceInfo get info => DeviceInfo(
        kind: 'glasses',
        id: deviceId,
        capabilities: capabilities.toWireCapabilities(),
      );

  @override
  Stream<Uint8List> get audio16k => _audioController.stream;

  @override
  Stream<Uint8List> get jpegFrames => _videoController.stream;

  @override
  Future<void> initialize() async {
    // TODO(transport): establish the BLE/RTSP/SDK connection to [endpoint].
    _log.info(
      'glasses stub initialized (endpoint=$endpoint) — no transport yet',
    );
  }

  @override
  Future<void> startAudio() async {
    // TODO(transport): subscribe to the device audio stream and forward
    // resampled PCM16 mono 16 kHz chunks via `_audioController.add(...)`.
    _audioRunning = true;
    _log.warn('startAudio: glasses transport not implemented — silent stream');
  }

  @override
  Future<void> stopAudio() async {
    _audioRunning = false;
  }

  @override
  Future<void> startVideo() async {
    // TODO(transport): pull ~1 fps stills, downscale ≤1024px, JPEG-encode, then
    // forward via `_videoController.add(...)`.
    _videoRunning = true;
    _log.warn('startVideo: glasses transport not implemented — silent stream');
  }

  @override
  Future<void> stopVideo() async {
    _videoRunning = false;
  }

  /// Whether the (future) transport is currently streaming. Exposed for UI/debug.
  bool get isAudioRunning => _audioRunning;
  bool get isVideoRunning => _videoRunning;

  @override
  Future<void> dispose() async {
    // TODO(transport): tear down the BLE/RTSP/SDK connection.
    await _audioController.close();
    await _videoController.close();
  }
}
