import 'dart:async';
import 'dart:typed_data';

import 'package:farryon/capture/glasses_capture_config.dart';
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
  Future<void> setRetentionDays(int days) async {}
  @override
  Future<void> requestBattery() async {}
  @override
  Future<void> requestDeviceInfo() async {}
  @override
  Future<void> takePhoto() async {}
  @override
  Future<String> takeAiPhoto() async {
    calls.add('takeAiPhoto');
    return 'req';
  }
  @override
  Future<String> startVideoRecording(int seconds) async => 'vid-req';
  @override
  Future<void> stopVideoRecording() async {}
  @override
  Future<void> setVideoDuration(int seconds) async {}
  @override
  Future<void> pairClassicBt() async {}
  @override
  Future<void> startWifiSync() async {}
  @override
  Future<void> stopWifiSync() async {}
  @override
  Future<void> refreshMediaCounts() async {}
  @override
  Future<void> setVolume(String type, int level) async {}
  @override
  Future<void> enableBluetooth() async => calls.add('enableBluetooth');
  @override
  Future<void> startMicService() async => calls.add('startMicService');
  @override
  Future<void> stopMicService() async => calls.add('stopMicService');
}

void main() {
  late _FakeBridge bridge;
  late GlassesCaptureSource src;

  // Tiny budgets so failure-path tests don't sit out real-time waits.
  // maxRetries=0 keeps most tests on the single-attempt path; the retry
  // behaviour has its own config + tests below.
  const testConfig = GlassesCaptureConfig(
    captureTimeout: Duration(milliseconds: 200),
    connectWait: Duration(milliseconds: 50),
    maxRetries: 0,
  );

  setUp(() {
    bridge = _FakeBridge();
    src = GlassesCaptureSource(bridge: bridge, config: testConfig);
  });

  Future<void> pump() => Future<void>.delayed(Duration.zero);

  group('hands-free glasses mic', () {
    setUp(() {
      GlassesCaptureSource.handsFreeMic = true;
      GlassesCaptureSource.handsFreeUnsupported = false;
      GlassesCaptureSource.handsFreeTransport = 'tws';
    });
    tearDown(() {
      GlassesCaptureSource.handsFreeUnsupported = false;
      GlassesCaptureSource.handsFreeTransport = 'call';
    });

    test('call mode is the default transport: the headset link, no press',
        () async {
      GlassesCaptureSource.handsFreeTransport = 'call';
      final bridge = _FakeBridge();
      final src = GlassesCaptureSource(bridge: bridge, config: testConfig);
      await src.initialize();
      await src.startAudio();
      expect(bridge.calls, contains('startAudioTest:call'));
      expect(bridge.calls, isNot(contains('startAudioTest:tws')));
      expect(bridge.calls, isNot(contains('startAudioTest:pcm')));
      // A stray tws status can never demote call mode.
      bridge.emit('audio', {'status': 'tws status=2', 'twsStatus': 2});
      await Future<void>.delayed(Duration.zero);
      expect(GlassesCaptureSource.handsFreeUnsupported, isFalse);
      expect(bridge.calls, isNot(contains('startAudioTest:pcm')));
    });

    test('call-mode PCM flows to audio16k like every glasses path', () async {
      GlassesCaptureSource.handsFreeTransport = 'call';
      final bridge = _FakeBridge();
      final src = GlassesCaptureSource(bridge: bridge, config: testConfig);
      await src.initialize();
      final got = <int>[];
      final sub = src.audio16k.listen((c) => got.add(c.length));
      await src.startAudio();
      bridge.emit('pcmChunk', {'bytes': 4, 'data': Uint8List(4)});
      await Future<void>.delayed(Duration.zero);
      expect(got, [4]);
      await sub.cancel();
    });

    test('starts in tws mode when hands-free is on', () async {
      final bridge = _FakeBridge();
      final src = GlassesCaptureSource(bridge: bridge, config: testConfig);
      await src.initialize();
      await src.startAudio();
      expect(bridge.calls, contains('startAudioTest:tws'));
      expect(bridge.calls, isNot(contains('startAudioTest:pcm')));
    });

    test("the SDK's 15 s timeout with no audio falls back to press-to-talk",
        () async {
      // L802 firmware AM01L2_2.00.00_260114: soundControl is unknown to the
      // glasses, nothing streams, the SDK stops itself with status 2.
      final bridge = _FakeBridge();
      final src = GlassesCaptureSource(bridge: bridge, config: testConfig);
      await src.initialize();
      await src.startAudio();
      bridge.emit('audio', {'status': 'tws status=2', 'twsStatus': 2});
      await Future<void>.delayed(Duration.zero);
      expect(bridge.calls, contains('startAudioTest:pcm'));
      expect(GlassesCaptureSource.handsFreeUnsupported, isTrue);
      // The verdict sticks: the next start skips the 15 s wait entirely.
      bridge.calls.clear();
      await src.stopAudio();
      await src.startAudio();
      expect(bridge.calls, contains('startAudioTest:pcm'));
      expect(bridge.calls, isNot(contains('startAudioTest:tws')));
    });

    test('a status-2 stop AFTER audio flowed is not a failure', () async {
      final bridge = _FakeBridge();
      final src = GlassesCaptureSource(bridge: bridge, config: testConfig);
      await src.initialize();
      await src.startAudio();
      bridge.emit('pcmChunk', {'bytes': 3, 'data': Uint8List.fromList([1, 2, 3])});
      await Future<void>.delayed(Duration.zero);
      bridge.emit('audio', {'status': 'tws status=2', 'twsStatus': 2});
      await Future<void>.delayed(Duration.zero);
      expect(bridge.calls, isNot(contains('startAudioTest:pcm')));
      expect(GlassesCaptureSource.handsFreeUnsupported, isFalse);
    });

    test('our own stop is never mistaken for a timeout', () async {
      final bridge = _FakeBridge();
      final src = GlassesCaptureSource(bridge: bridge, config: testConfig);
      await src.initialize();
      await src.startAudio();
      await src.stopAudio();
      bridge.emit('audio', {'status': 'tws status=2', 'twsStatus': 2});
      await Future<void>.delayed(Duration.zero);
      expect(bridge.calls, isNot(contains('startAudioTest:pcm')));
      expect(GlassesCaptureSource.handsFreeUnsupported, isFalse);
    });
  });

  test('advertises audio-in and (photo-trigger) video-in', () {
    expect(src.capabilities.audioIn, isTrue);
    // B3: vision is on — not a continuous stream, but an on-demand photo whose
    // thumbnail is emitted on jpegFrames.
    expect(src.capabilities.videoIn, isTrue);
    expect(src.info.kind, 'glasses');
  });

  test('B3: capturePhoto triggers an AI photo whose thumbnail becomes a frame',
      () async {
    // Subscribe to bridge events, then mark connected (capturePhoto needs it).
    await src.initialize();
    bridge.emit('connectionState', {'state': 'connected', 'mac': 'C0:97:AA'});
    await pump();

    final frames = <Uint8List>[];
    final sub = src.jpegFrames.listen(frames.add);

    final pending = src.capturePhoto();
    await pump();
    expect(bridge.calls, contains('takeAiPhoto'));

    // The bridge later delivers the thumbnail — it should surface as a frame
    // AND resolve the request with the same bytes (requestId-correlated).
    final jpeg = Uint8List.fromList([1, 2, 3, 4]);
    bridge.emit('thumbnail', {'requestId': 'req', 'jpeg': jpeg});
    await pump();

    expect(frames, hasLength(1));
    expect(frames.first, jpeg);
    final result = await pending;
    expect(result.ok, isTrue);
    expect(result.jpeg, jpeg);
    await sub.cancel();
  });

  test('capturePhoto fails fast with notConnected while disconnected',
      () async {
    await src.initialize();
    final result = await src.capturePhoto();
    expect(result.ok, isFalse);
    expect(result.failure, GlassesCaptureFailure.notConnected);
    expect(bridge.calls, isNot(contains('takeAiPhoto')));
  });

  test('capturePhoto waits out a connect in progress, then captures',
      () async {
    await src.initialize();
    final pending = src.capturePhoto(); // not connected yet
    await pump();
    expect(bridge.calls, isNot(contains('takeAiPhoto')));

    // The auto-connect lands within the connect-wait window.
    bridge.emit('connectionState', {'state': 'connected', 'mac': 'C0:97:AA'});
    await pump();
    await pump(); // status wait resolves, then the command is issued
    expect(bridge.calls, contains('takeAiPhoto'));

    bridge.emit(
        'thumbnail', {'requestId': 'req', 'jpeg': Uint8List.fromList([7])});
    final result = await pending;
    expect(result.ok, isTrue);
  });

  test('duplicate capturePhoto joins the in-flight request (one BLE command)',
      () async {
    await src.initialize();
    bridge.emit('connectionState', {'state': 'connected', 'mac': 'C0:97:AA'});
    await pump();

    final first = src.capturePhoto();
    final second = src.capturePhoto();
    await pump();
    expect(bridge.calls.where((c) => c == 'takeAiPhoto'), hasLength(1));

    bridge.emit(
        'thumbnail', {'requestId': 'req', 'jpeg': Uint8List.fromList([1])});
    final results = await Future.wait([first, second]);
    expect(results[0].ok, isTrue);
    expect(results[1].ok, isTrue);
    expect(identical(results[0], results[1]), isTrue);
  });

  test('a native captureFailed event resolves the request with its reason',
      () async {
    await src.initialize();
    bridge.emit('connectionState', {'state': 'connected', 'mac': 'C0:97:AA'});
    await pump();

    final pending = src.capturePhoto();
    await pump();
    bridge.emit('captureFailed', {
      'requestId': 'req',
      'reason': 'transfer_stalled',
      'detail': 'no thumbnail chunk within 3000 ms',
    });
    final result = await pending;
    expect(result.ok, isFalse);
    expect(result.failure, GlassesCaptureFailure.transferStalled);
  });

  test('capturePhoto times out with captureTimeout when nothing arrives',
      () async {
    await src.initialize();
    bridge.emit('connectionState', {'state': 'connected', 'mac': 'C0:97:AA'});
    await pump();

    final result = await src.capturePhoto();
    expect(result.ok, isFalse);
    expect(result.failure, GlassesCaptureFailure.captureTimeout);
  });

  test('a disconnect mid-capture fails the request with notConnected',
      () async {
    await src.initialize();
    bridge.emit('connectionState', {'state': 'connected', 'mac': 'C0:97:AA'});
    await pump();

    final pending = src.capturePhoto();
    await pump();
    bridge.emit('connectionState', {'state': 'disconnected'});
    final result = await pending;
    expect(result.ok, isFalse);
    expect(result.failure, GlassesCaptureFailure.notConnected);
  });

  test('a thumbnail for a DIFFERENT request does not resolve the in-flight one',
      () async {
    await src.initialize();
    bridge.emit('connectionState', {'state': 'connected', 'mac': 'C0:97:AA'});
    await pump();

    final pending = src.capturePhoto();
    await pump();
    // A device-initiated (gesture) photo lands mid-request: it must still be
    // emitted as a frame, but must NOT complete the app's request.
    bridge.emit('thumbnail', {
      'requestId': 'device-initiated',
      'jpeg': Uint8List.fromList([9, 9]),
    });
    await pump();
    bridge.emit(
        'thumbnail', {'requestId': 'req', 'jpeg': Uint8List.fromList([1])});
    final result = await pending;
    expect(result.ok, isTrue);
    expect(result.jpeg, [1]);
  });

  test('a transfer_stalled failure auto-retries and then succeeds', () async {
    // The glasses occasionally double-fire the capture notify and stall the
    // transfer; a clean re-capture works. One retry should turn that into a
    // delivered photo with no error surfaced.
    const retryConfig = GlassesCaptureConfig(
      captureTimeout: Duration(milliseconds: 500),
      connectWait: Duration(milliseconds: 50),
      maxRetries: 1,
      retryDelay: Duration(milliseconds: 10),
    );
    final s = GlassesCaptureSource(bridge: bridge, config: retryConfig);
    await s.initialize();
    bridge.emit('connectionState', {'state': 'connected', 'mac': 'C0:97:AA'});
    await pump();

    final pending = s.capturePhoto();
    await pump();
    // First attempt stalls…
    bridge.emit('captureFailed', {'requestId': 'req', 'reason': 'transfer_stalled'});
    await Future<void>.delayed(const Duration(milliseconds: 30)); // retry fires
    expect(bridge.calls.where((c) => c == 'takeAiPhoto'), hasLength(2));
    // …the retry delivers the photo.
    bridge.emit('thumbnail', {'requestId': 'req', 'jpeg': Uint8List.fromList([7])});
    final result = await pending;
    expect(result.ok, isTrue);
    expect(result.jpeg, [7]);
    await s.dispose();
  });

  test('retries are bounded: repeated stalls end in a transferStalled failure',
      () async {
    const retryConfig = GlassesCaptureConfig(
      captureTimeout: Duration(milliseconds: 500),
      connectWait: Duration(milliseconds: 50),
      maxRetries: 1,
      retryDelay: Duration(milliseconds: 10),
    );
    final s = GlassesCaptureSource(bridge: bridge, config: retryConfig);
    await s.initialize();
    bridge.emit('connectionState', {'state': 'connected', 'mac': 'C0:97:AA'});
    await pump();

    final pending = s.capturePhoto();
    await pump();
    bridge.emit('captureFailed', {'requestId': 'req', 'reason': 'transfer_stalled'});
    await Future<void>.delayed(const Duration(milliseconds: 30)); // retry fires
    // Retry stalls too → give up with the real reason (no infinite loop).
    bridge.emit('captureFailed', {'requestId': 'req', 'reason': 'transfer_stalled'});
    final result = await pending;
    expect(result.ok, isFalse);
    expect(result.failure, GlassesCaptureFailure.transferStalled);
    expect(bridge.calls.where((c) => c == 'takeAiPhoto'), hasLength(2));
    await s.dispose();
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

    GlassesCaptureSource.handsFreeMic = false;
    await src.startAudio();
    expect(bridge.calls, contains('startAudioTest:pcm'));
    GlassesCaptureSource.handsFreeMic = true;
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
