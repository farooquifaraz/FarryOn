import 'dart:async';
import 'dart:typed_data';

import 'package:farryon/capture/capture_source.dart';
import 'package:farryon/capture/device_registry.dart';
import 'package:farryon/core/config.dart';
import 'package:farryon/features/glasses_lab/bridge/glasses_channel.dart';
import 'package:farryon/data/live_client.dart';
import 'package:farryon/playback/pcm_player.dart';
import 'package:farryon/protocol/frames.dart';
import 'package:farryon/protocol/messages.dart';
import 'package:farryon/protocol/protocol.dart';
import 'package:farryon/state/live_controller.dart';
import 'package:farryon/state/permissions.dart';
import 'package:flutter_test/flutter_test.dart';

import 'live_client_test.dart' show FakeChannel;

/// In-memory capture source the test can pump audio/video through.
class FakeCaptureSource implements CaptureSource {
  final audioCtl = StreamController<Uint8List>.broadcast();
  final videoCtl = StreamController<Uint8List>.broadcast();
  bool audioStarted = false;
  bool videoStarted = false;

  @override
  CaptureCapabilities get capabilities =>
      const CaptureCapabilities(audioIn: true, videoIn: true);

  @override
  DeviceInfo get info => const DeviceInfo(
        kind: 'phone',
        id: 'fake',
        capabilities: ['audio_in', 'video_in', 'audio_out'],
      );

  @override
  Stream<Uint8List> get audio16k => audioCtl.stream;
  @override
  Stream<Uint8List> get jpegFrames => videoCtl.stream;

  @override
  Future<void> initialize() async {}
  @override
  Future<void> startAudio() async => audioStarted = true;
  @override
  Future<void> stopAudio() async => audioStarted = false;
  @override
  Future<void> startVideo() async => videoStarted = true;
  @override
  Future<void> stopVideo() async => videoStarted = false;
  @override
  Future<void> releaseCamera() async {}
  @override
  Future<void> setPortrait(bool portrait) async => this.portrait = portrait;
  @override
  Future<double> setZoom(double level) async => zoom = level;
  @override
  Future<void> dispose() async {
    await audioCtl.close();
    await videoCtl.close();
  }

  bool portrait = true;
  double zoom = 1.0;
}

/// PcmPlayer test double that records feed/flush without touching audio HW.
class FakePcmPlayer implements PcmPlayer {
  final fed = <Uint8List>[];
  int flushCount = 0;

  @override
  Future<void> feed(Uint8List pcm16) async => fed.add(pcm16);
  @override
  Future<void> flush() async => flushCount++;
  @override
  Future<void> initialize() async {}
  @override
  Future<void> start() async {}
  @override
  Future<void> stop() async {}
  @override
  Future<void> dispose() async {}
}

/// Fake glasses bridge: records connect calls and lets the test push events.
class FakeGlassesBridge implements GlassesBridgeApi {
  final connectCalls = <String>[];
  final _events = StreamController<GlassesLabEvent>.broadcast();
  Map<String, Object?> info = const {'implementation': 'stub', 'lastMac': 'AA:BB:CC'};

  void emit(String type, Map<String, Object?> data) =>
      _events.add(GlassesLabEvent(type: type, data: data));

  @override
  Stream<GlassesLabEvent> events() => _events.stream;
  @override
  Future<Map<String, Object?>> bridgeInfo() async => info;
  @override
  Future<void> connect(String mac) async => connectCalls.add(mac);
  @override
  Future<List<GlassesDeviceHit>> scan({Duration timeout = Duration.zero}) async =>
      const [];
  @override
  Future<void> disconnect() async {}
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
  Future<void> startAudioTest(String mode) async {}
  @override
  Future<void> stopAudioTest() async {}
  @override
  Future<void> startWifiSync() async {}
  @override
  Future<void> stopWifiSync() async {}
  @override
  Future<void> setVolume(String type, int level) async {}
  @override
  Future<void> enableBluetooth() async {}
}

class GrantingPermissions implements PermissionsService {
  @override
  Future<bool> hasMicAndCamera() async => true;
  @override
  Future<void> openSettings() async {}
  @override
  Future<PermissionOutcome> requestMicAndCamera() async =>
      PermissionOutcome.granted;
}

void main() {
  late FakeChannel fake;
  late FakeCaptureSource source;
  late FakePcmPlayer player;
  late DeviceRegistry registry;
  late LiveController controller;

  setUp(() {
    fake = FakeChannel();
    source = FakeCaptureSource();
    player = FakePcmPlayer();
    registry = DeviceRegistry(factory: (_) => source);

    WebSocketLiveClient clientFactory(
      AppConfig cfg,
      DeviceInfo Function() deviceInfo,
    ) =>
        WebSocketLiveClient(
          config: cfg,
          platform: 'android',
          deviceInfoProvider: deviceInfo,
          channelFactory: (_) => fake,
        );

    controller = LiveController(
      config: const AppConfig(host: 'h', port: 8000, secure: false),
      registry: registry,
      player: player,
      permissions: GrantingPermissions(),
      clientFactory: clientFactory,
      platform: 'android',
    );
  });

  tearDown(() => controller.dispose());

  Future<void> tick() => Future<void>.delayed(Duration.zero);

  test('connect starts video and opens the socket', () async {
    final outcome = await controller.connect();
    await tick();
    expect(outcome, PermissionOutcome.granted);
    expect(source.videoStarted, isTrue);
    expect(controller.state.cameraOn, isTrue);
  });

  test('captured JPEG frames become 0x02 frames on the wire', () async {
    await controller.connect();
    await tick();
    source.videoCtl.add(Uint8List.fromList([1, 2, 3]));
    await tick();

    final binary = fake.sentLog.whereType<Uint8List>().toList();
    expect(binary, isNotEmpty);
    final frame = MediaFrame.decode(binary.last);
    expect(frame.tag, FrameTag.inputVideo);
    expect(frame.payload, equals(Uint8List.fromList([1, 2, 3])));
  });

  test('startListening sends audio_start and pipes mic PCM to 0x01', () async {
    await controller.connect();
    await tick();
    await controller.startListening();
    await tick();

    expect(controller.state.micOpen, isTrue);
    expect(source.audioStarted, isTrue);

    source.audioCtl.add(Uint8List.fromList([5, 6]));
    await tick();

    final audioFrames = fake.sentLog
        .whereType<Uint8List>()
        .map(MediaFrame.decode)
        .where((f) => f.tag == FrameTag.inputAudio)
        .toList();
    expect(audioFrames, isNotEmpty);
    expect(audioFrames.last.payload, equals(Uint8List.fromList([5, 6])));

    // audio_start JSON was sent.
    final hasAudioStart = fake.sentLog
        .whereType<String>()
        .any((s) => s.contains('"type":"audio_start"'));
    expect(hasAudioStart, isTrue);
  });

  test('OUTPUT_AUDIO frames are fed to the player', () async {
    await controller.connect();
    await tick();

    fake.pushBinary(MediaFrame.encode(
      tag: FrameTag.outputAudio,
      timestampMs: 1,
      payload: Uint8List.fromList([1, 1, 1]),
    ));
    await tick();

    expect(player.fed.single, equals(Uint8List.fromList([1, 1, 1])));
  });

  test('interrupt while speaking flushes playback + sends interrupt',
      () async {
    await controller.connect();
    await tick();

    // Simulate the assistant speaking.
    fake.pushJson({'type': 'audio_start'});
    await tick();
    expect(controller.state.liveState, LiveState.speaking);

    // Hands-free: barge-in is the stop control, which calls interrupt().
    await controller.interrupt();
    await tick();

    expect(player.flushCount, greaterThanOrEqualTo(1));
    final hasInterrupt = fake.sentLog
        .whereType<String>()
        .any((s) => s.contains('"type":"interrupt"'));
    expect(hasInterrupt, isTrue);
  });

  test('mic auto-opens on connect (hands-free)', () async {
    await controller.connect();
    await tick();
    expect(controller.state.micOpen, isTrue);
  });

  test('tool_call then tool_result update tool activity', () async {
    await controller.connect();
    await tick();

    fake.pushJson({
      'type': 'tool_call',
      'id': 'c1',
      'name': 'create_note',
      'args': {'text': 'hi'},
      'needsPermission': false,
    });
    await tick();
    expect(controller.state.tools.single.isPending, isTrue);

    fake.pushJson({
      'type': 'tool_result',
      'id': 'c1',
      'name': 'create_note',
      'ok': true,
      'result': {'id': 7},
    });
    await tick();
    final tool = controller.state.tools.single;
    expect(tool.isPending, isFalse);
    expect(tool.ok, isTrue);
    expect(tool.result, {'id': 7});
  });

  test('transcript fragments merge while non-final', () async {
    await controller.connect();
    await tick();

    fake.pushJson({
      'type': 'transcript',
      'role': 'assistant',
      'text': 'Hel',
      'final': false,
    });
    fake.pushJson({
      'type': 'transcript',
      'role': 'assistant',
      'text': 'Hello',
      'final': true,
    });
    await tick();

    expect(controller.state.transcripts.length, 1);
    expect(controller.state.transcripts.single.text, 'Hello');
    expect(controller.state.transcripts.single.isFinal, isTrue);
  });

  test('transcript list is capped so long sessions stay fast', () async {
    await controller.connect();
    await tick();

    // Push many final lines, alternating roles so each becomes its own entry.
    for (var i = 0; i < 200; i++) {
      fake.pushJson({
        'type': 'transcript',
        'role': i.isEven ? 'user' : 'assistant',
        'text': 'line $i',
        'final': true,
      });
    }
    await tick();

    expect(controller.state.transcripts.length, lessThanOrEqualTo(80));
    // The newest line is retained; the oldest are dropped.
    expect(controller.state.transcripts.last.text, 'line 199');
    expect(
      controller.state.transcripts.any((t) => t.text == 'line 0'),
      isFalse,
    );
  });

  test('sendText optimistically appends a user line and sends text', () async {
    await controller.connect();
    await tick();
    controller.sendText('  hi there  ');
    await tick();

    expect(controller.state.transcripts.single.text, 'hi there');
    final sentText = fake.sentLog
        .whereType<String>()
        .any((s) => s.contains('"type":"text"') && s.contains('hi there'));
    expect(sentText, isTrue);
  });

  test('connect_glasses can reconnect after a drop (guard is not stuck)',
      () async {
    // Regression: after a successful connect the in-flight guard used to stay
    // set forever, silently blocking every later reconnect (e.g. a second
    // session started without killing the app). This drives connect → drop →
    // reconnect and asserts the second connect is actually attempted.
    final glasses = FakeGlassesBridge();
    final source = FakeCaptureSource();
    final registry = DeviceRegistry(factory: (_) => source);
    final ctl = LiveController(
      config: const AppConfig(host: 'h', port: 8000, secure: false),
      registry: registry,
      player: FakePcmPlayer(),
      permissions: GrantingPermissions(),
      clientFactory: (cfg, deviceInfo) => WebSocketLiveClient(
        config: cfg,
        platform: 'android',
        deviceInfoProvider: deviceInfo,
        channelFactory: (_) => fake,
      ),
      platform: 'android',
      glassesBridge: glasses,
    );
    addTearDown(ctl.dispose);

    await ctl.connect();
    await tick();

    // First connect_glasses tool call → bridge.connect attempted.
    fake.pushJson({
      'type': 'tool_call',
      'id': 'g1',
      'name': 'connect_glasses',
      'args': <String, dynamic>{},
      'needsPermission': false,
    });
    await tick();
    expect(glasses.connectCalls, hasLength(1));

    // Link comes up, then drops (BLE lost between sessions).
    glasses.emit('connectionState', {'state': 'connected', 'mac': 'AA:BB:CC'});
    await tick();
    glasses.emit('connectionState', {'state': 'disconnected'});
    await tick();

    // Second connect_glasses (new session) MUST attempt a connect again.
    fake.pushJson({
      'type': 'tool_call',
      'id': 'g2',
      'name': 'connect_glasses',
      'args': <String, dynamic>{},
      'needsPermission': false,
    });
    await tick();
    expect(glasses.connectCalls, hasLength(2),
        reason: 'reconnect after a drop must not be blocked by a stuck guard');
  });
}
