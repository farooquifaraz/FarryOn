import 'dart:typed_data';

import '../protocol/messages.dart';

/// The universal capture-device adapter — the seam that lets FarryOn run on a
/// phone today and on smart glasses (BLE/RTSP/Wi-Fi) tomorrow.
///
/// **The entire app depends only on this abstraction**, never on the phone
/// camera/mic directly. To support a new device you implement [CaptureSource]
/// and register it (see `device_registry.dart`); nothing in the data, state, or
/// UI layers needs to change.
///
/// Contract:
///   * [audio16k] emits PCM signed-16 LE **mono 16 kHz** chunks (20–100 ms),
///     ready to be wrapped in an INPUT_AUDIO (0x01) frame.
///   * [jpegFrames] emits JPEG-encoded still frames at ~1 fps, already
///     downscaled to ≤ 1024 px, ready for INPUT_VIDEO (0x02).
///   * Both streams are broadcast and only produce data between [startAudio]/
///     [startVideo] and their stops.
///
/// Implementations must already do any required resampling/encoding so the rest
/// of the pipeline can treat their output as wire-ready payloads.
abstract class CaptureSource {
  /// Static description of this device for the `hello.device` field.
  DeviceInfo get info;

  /// Whether this source can produce each stream. Lets the UI hide controls a
  /// device cannot support (e.g. an audio-only earpiece has no video).
  CaptureCapabilities get capabilities;

  /// PCM16 LE mono 16 kHz audio chunks (active only while audio is started).
  Stream<Uint8List> get audio16k;

  /// JPEG still frames at ~1 fps, downscaled ≤ 1024 px (active while video is
  /// started).
  Stream<Uint8List> get jpegFrames;

  /// Acquire any device handles (e.g. open the camera). Safe to call once
  /// before starting streams; implementations should be idempotent.
  Future<void> initialize();

  /// Start streaming microphone audio on [audio16k].
  Future<void> startAudio();

  /// Stop streaming microphone audio.
  Future<void> stopAudio();

  /// Start streaming camera frames on [jpegFrames].
  Future<void> startVideo();

  /// Stop streaming camera frames.
  Future<void> stopVideo();

  /// Release all device resources. The source may be re-[initialize]d later.
  Future<void> dispose();
}

/// Declares which streams a [CaptureSource] can produce.
class CaptureCapabilities {
  const CaptureCapabilities({
    required this.audioIn,
    required this.videoIn,
  });

  final bool audioIn;
  final bool videoIn;

  /// Map onto the `device.capabilities` wire list. `audio_out` is intentionally
  /// not advertised here: playback is the app's responsibility, not the capture
  /// device's, for phone sources. Glasses that own a speaker may add it.
  List<String> toWireCapabilities({bool audioOut = true}) => [
        if (audioIn) 'audio_in',
        if (videoIn) 'video_in',
        if (audioOut) 'audio_out',
      ];
}
