import 'dart:async';
import 'dart:typed_data';

import 'package:farryon/capture/glasses_capture_source.dart';
import 'package:farryon/features/glasses_lab/bridge/glasses_channel.dart';
import 'package:flutter_test/flutter_test.dart';

/// Fake bridge: records commands, lets the test push events.
class _FakeBridge implements GlassesBridgeApi {
  final calls = <String>[];
  final events_ = StreamController<GlassesLabEvent>.broadcast();
  Map<String, Object?> info = const {'implementation': 'stub'};

  void emit(String type, Map<String, Object?> data) =>
      events_.add(GlassesLabEvent(type: type, data: data));

  @override
  Future<Map<String, Object?>> bridgeInfo() async {
    calls.add('bridgeInfo');
    return info;
  }

  @override
  Future<void> connect(String mac) async => calls.add('connect:$mac');
  @override
  Future<void> startAudioTest(String mode) async =>
      calls.add('startAudioTest:$mode');
  @override
  Future<void> stopAudioTest() async => calls.add('stopAudioTest');
  @override
  Future<void> disconnect() async => calls.add('disconnect');
  @override
  Stream<GlassesLabEvent> events() => events_.stream;

  // Unused by the capture source.
  @override
  Future<List<GlassesDeviceHit>> scan({Duration timeout = Duration.zero}) async =>
      const [];
  @override
  Future<void> setAutoReconnect(bool enabled) async {}
  @override
  Future<void> requestBattery() async {}
  @override
  Future<void> requestDeviceInfo() async {}
  @override
  Future<void> takePhoto() async {}
  @override
  Future<String> takeAiPhoto() async => 'req';
  @override
  Future<void> pairClassicBt() async {}
  @override
  Future<void> startWifiSync() async {}
  @override
  Future<void> stopWifiSync() async {}
  @override
  Future<void> setVolume(String type, int level) async {}
  @override
  Future<void> enableBluetooth() async => calls.add('enableBluetooth');
}

void main() {
  late _FakeBridge bridge;
  late GlassesCaptureSource src;

  setUp(() {
    bridge = _FakeBridge();
    src = GlassesCaptureSource(bridge: bridge);
  });

  Future<void> pump() => Future<void>.delayed(Duration.zero);

  test('advertises audio-in only (no continuous video on this hardware)', () {
    expect(src.capabilities.audioIn, isTrue);
    expect(src.capabilities.videoIn, isFalse);
    expect(src.info.kind, 'glasses');
  });

  test('initialize auto-connects to the saved MAC', () async {
    bridge.info = {'implementation': 'heycyan', 'lastMac': 'C0:97:AA'};
    await src.initialize();
    await pump();
    expect(bridge.calls, contains('connect:C0:97:AA'));
  });

  test('initialize skips connect when no saved device', () async {
    await src.initialize();
    await pump();
    expect(bridge.calls.where((c) => c.startsWith('connect')), isEmpty);
  });

  test('PCM chunks forward to audio16k only while audio is running', () async {
    await src.initialize();
    final got = <Uint8List>[];
    final sub = src.audio16k.listen(got.add);

    // Not started yet → dropped.
    bridge.emit('pcmChunk', {'bytes': 4, 'data': Uint8List.fromList([1, 2, 3, 4])});
    await pump();
    expect(got, isEmpty);

    await src.startAudio();
    expect(bridge.calls, contains('startAudioTest:pcm'));
    bridge.emit('pcmChunk', {'bytes': 4, 'data': Uint8List.fromList([9, 8, 7, 6])});
    await pump();
    expect(got, hasLength(1));
    expect(got.first, [9, 8, 7, 6]);

    await src.stopAudio();
    expect(bridge.calls, contains('stopAudioTest'));
    bridge.emit('pcmChunk', {'bytes': 2, 'data': Uint8List.fromList([0, 0])});
    await pump();
    expect(got, hasLength(1)); // still just the one from while-running

    await sub.cancel();
  });

  test('tracks connection state from events', () async {
    await src.initialize();
    expect(src.isConnected, isFalse);
    bridge.emit('connectionState', {'state': 'connected', 'mac': 'C0:97:AA'});
    await pump();
    expect(src.isConnected, isTrue);
    bridge.emit('connectionState', {'state': 'disconnected'});
    await pump();
    expect(src.isConnected, isFalse);
  });
}
