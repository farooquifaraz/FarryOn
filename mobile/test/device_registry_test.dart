import 'dart:typed_data';

import 'package:farryon/capture/capture_source.dart';
import 'package:farryon/capture/device_registry.dart';
import 'package:farryon/protocol/messages.dart';
import 'package:flutter_test/flutter_test.dart';

/// Trivial fake source that records which kind created it.
class _FakeSource implements CaptureSource {
  _FakeSource(this.tag);
  final String tag;
  @override
  CaptureCapabilities get capabilities =>
      const CaptureCapabilities(audioIn: true, videoIn: true);
  @override
  DeviceInfo get info => DeviceInfo(kind: tag, id: tag, capabilities: const []);
  @override
  Stream<Uint8List> get audio16k => const Stream.empty();
  @override
  Stream<Uint8List> get jpegFrames => const Stream.empty();
  @override
  Future<void> initialize() async {}
  @override
  Future<void> startAudio() async {}
  @override
  Future<void> stopAudio() async {}
  @override
  Future<void> startVideo() async {}
  @override
  Future<void> stopVideo() async {}
  @override
  Future<void> releaseCamera() async {}
  @override
  Future<void> setPortrait(bool portrait) async {}
  @override
  Future<double> setZoom(double level) async => level;
  @override
  Future<void> dispose() async {}
}

void main() {
  DeviceRegistry make() => DeviceRegistry(
        factory: (kind) => _FakeSource(kind.name),
      );

  test('defaults both channels to phone, sharing one instance', () {
    final r = make();
    expect(r.audioKind, CaptureDeviceKind.phone);
    expect(r.videoKind, CaptureDeviceKind.phone);
    expect(identical(r.audioSource, r.videoSource), isTrue);
  });

  test('audio and video select independently (earbuds mic + phone camera '
      'stays one, glasses mic + phone camera splits)', () {
    final r = make();
    r.setAudioKind(CaptureDeviceKind.glasses);
    expect(r.audioKind, CaptureDeviceKind.glasses);
    expect(r.videoKind, CaptureDeviceKind.phone);
    expect(identical(r.audioSource, r.videoSource), isFalse);
    expect((r.audioSource as _FakeSource).tag, 'glasses');
    expect((r.videoSource as _FakeSource).tag, 'phone');
  });

  test('sources are cached — reselecting the same kind reuses the instance',
      () {
    final r = make();
    final first = r.videoSource;
    r.setAudioKind(CaptureDeviceKind.glasses);
    r.setAudioKind(CaptureDeviceKind.phone);
    // Audio back to phone → same cached phone source as video.
    expect(identical(r.audioSource, first), isTrue);
  });
}
