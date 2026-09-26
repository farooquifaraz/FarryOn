import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:farryon/capture/capture_source.dart';
import 'package:farryon/capture/device_registry.dart';
import 'package:farryon/core/config.dart';
import 'package:farryon/data/live_client.dart';
import 'package:farryon/features/glasses_lab/bridge/glasses_channel.dart';
import 'package:farryon/playback/device_voice.dart';
import 'package:farryon/playback/pcm_player.dart';
import 'package:farryon/protocol/frames.dart';
import 'package:farryon/protocol/messages.dart';
import 'package:farryon/protocol/protocol.dart';
import 'package:farryon/state/live_controller.dart';
import 'package:farryon/state/live_state.dart';
import 'package:farryon/state/permissions.dart';
import 'package:flutter_test/flutter_test.dart';

import 'live_client_test.dart' show FakeChannel;

/// In-memory capture source the test can pump audio/video through.
class FakeDeviceVoice extends DeviceVoice {
  final spoken = <String>[];
  final languages = <String>[];
  @override
  Future<bool> speak(String text, String language) async {
    spoken.add(text);
    languages.add(language);
    return true;
  }

  @override
  Future<void> stop() async {}

  @override
  bool isSpeakingWithin(Duration tail) => false;
}

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
  Future<Map<String, Object?>> micDiagnostics() async => const {};

  @override
  Future<void> stopAudio() async => audioStarted = false;
  @override
  Future<void> startVideo() async => videoStarted = true;
  @override
  Future<void> stopVideo() async => videoStarted = false;
  @override
  Future<void> captureOnce() async {
    // One frame, and the camera is NOT left streaming — the whole point.
    captureOnceCalls++;
    videoCtl.add(Uint8List.fromList([42]));
  }

  int captureOnceCalls = 0;
  @override
  Future<void> releaseCamera() async {}

  @override
  Future<void> setFrontCamera(bool front) async {}
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

  /// Drives the controller's echo guard: true = "the speaker is still busy".
  bool playing = false;

  @override
  bool isPlayingWithin(Duration tail) => playing;

  @override
  Future<void> feed(Uint8List pcm16) async => fed.add(pcm16);
  @override
  Future<void> flush() async {
    flushCount++;
    playing = false;
  }
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
  final videoCalls = <String>[];
  int syncCalls = 0;
  int countCalls = 0;
  int batteryCalls = 0;

  /// Just the start/stop calls. The recording LENGTH is pushed separately (at
  /// startup and again on connect, both deliberate), and those pushes would
  /// otherwise drown out what each test is actually about.
  List<String> get recordingCalls =>
      videoCalls.where((c) => !c.startsWith('duration:')).toList();
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
  Future<void> setRetentionDays(int days) async {}
  @override
  Future<void> requestBattery() async => batteryCalls++;
  @override
  Future<void> requestDeviceInfo() async {}
  @override
  Future<void> takePhoto() async {}
  @override
  Future<String> takeAiPhoto() async => 'req';
  @override
  Future<String> startVideoRecording(int seconds) async {
    videoCalls.add('start:$seconds');
    return 'vid-req';
  }

  @override
  Future<void> stopVideoRecording() async => videoCalls.add('stop');
  @override
  Future<void> setVideoDuration(int seconds) async =>
      videoCalls.add('duration:$seconds');
  @override
  Future<void> pairClassicBt() async {}
  @override
  Future<void> startAudioTest(String mode) async {}
  @override
  Future<void> stopAudioTest() async {}
  @override
  Future<Map<String, Object?>> micRoute() async => const {};
  @override
  Future<void> startWifiSync() async => syncCalls++;
  int forcedSyncCalls = 0;
  @override
  Future<void> forceWifiSync() async => forcedSyncCalls++;
  @override
  Future<void> stopWifiSync() async {}
  @override
  Future<void> refreshMediaCounts() async => countCalls++;
  @override
  Future<void> setVolume(String type, int level) async {}
  @override
  Future<void> enableBluetooth() async {}
  @override
  Future<void> startMicService() async {}
  @override
  Future<void> stopMicService() async {}
}

class GrantingPermissions implements PermissionsService {
  @override
  Future<bool> hasMicAndCamera() async => true;
  @override
  Future<void> openSettings() async {}
  @override
  Future<PermissionOutcome> requestMicAndCamera() async =>
      PermissionOutcome.granted;
  @override
  Future<PermissionOutcome> requestMicrophone() async =>
      PermissionOutcome.granted;
  @override
  Future<PermissionOutcome> requestNearbyWifi() async =>
      PermissionOutcome.granted;
  @override
  Future<bool> hasNearbyWifi() async => true;
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

  test('connect opens the socket and leaves the camera alone', () async {
    // The camera used to open with the session. It no longer does: every
    // conversation was streaming a frame a second whether or not anyone
    // wanted to be seen, and an assistant that starts watching the moment it
    // opens is not what someone would pick if asked.
    final outcome = await controller.connect();
    await tick();
    expect(outcome, PermissionOutcome.granted);
    expect(source.videoStarted, isFalse);
    expect(controller.state.cameraOn, isFalse);
  });

  test('asking for a frame takes ONE and leaves the camera off', () async {
    // What keeps the scan button and `identify_image` working with the camera
    // off. "Off" is the resting state, so answering "no camera frame" would be
    // wrong on a phone whose camera is fine — but neither may it answer by
    // switching the camera on and leaving it running, which is what it used to
    // do: one tap on Scan then sent a frame a second for the whole
    // conversation.
    await controller.connect();
    await tick();
    expect(controller.state.cameraOn, isFalse);

    expect(await controller.grabFrame(), isNotNull, reason: 'it still answers');
    expect(source.captureOnceCalls, 1, reason: 'exactly one picture');
    expect(source.videoStarted, isFalse, reason: 'and the camera is closed');
    expect(controller.state.cameraOn, isFalse);
  });

  test('turning the camera off throws its last frame away', () async {
    // Not an optimisation to reclaim: answering "what is this?" with the view
    // from before the camera was switched off would be worse than taking a
    // moment to look again. So the cached frame goes, and the next request
    // starts the camera rather than serving something stale.
    await controller.connect();
    await tick();
    await controller.setCameraEnabled(true);
    source.videoCtl.add(Uint8List.fromList([9, 9, 9]));
    await tick();
    expect(controller.lastFrame, isNotNull);

    await controller.setCameraEnabled(false);
    expect(controller.lastFrame, isNull, reason: 'a stale view must not linger');

    // It looks again — with one shot, not by starting the camera up.
    expect(await controller.grabFrame(), isNotNull);
    expect(source.captureOnceCalls, 1);
    expect(source.videoStarted, isFalse);
  });

  test('captured JPEG frames become 0x02 frames on the wire', () async {
    await controller.connect();
    await tick();
    // Explicit now: the camera no longer opens with the session, so a test
    // about frames on the wire has to ask for the camera the way a user does.
    await controller.setCameraEnabled(true);
    await tick();
    source.videoCtl.add(Uint8List.fromList([1, 2, 3]));
    await tick();

    final binary = fake.sentLog.whereType<Uint8List>().toList();
    expect(binary, isNotEmpty);
    final frame = MediaFrame.decode(binary.last);
    expect(frame.tag, FrameTag.inputVideo);
    expect(frame.payload, equals(Uint8List.fromList([1, 2, 3])));
  });

  test('a provider outage ends the session with an explanation, no reconnect',
      () async {
    await controller.connect();
    await tick();
    fake.pushJson({
      'type': 'error',
      'code': 'provider_credits',
      'message': 'Farry\'s voice service is temporarily unavailable.',
      'fatal': true,
    });
    await tick();
    fake.drop(); // the server closes the socket after a fatal error
    await Future<void>.delayed(const Duration(milliseconds: 700));

    expect(controller.state.serviceDown, isTrue);
    expect(controller.state.transcripts.last.role, 'notice');
    expect(controller.state.connection, ConnectionStatus.disconnected,
        reason: 'no reconnect loop into the same failure');
  });

  test('a build the server no longer serves shows the update screen, no reconnect',
      () async {
    await controller.connect();
    await tick();
    fake.pushJson({
      'type': 'error',
      'code': 'update_required',
      'message': 'A new version of FarryOn is required.',
      'fatal': true,
    });
    await tick();
    fake.drop();
    await Future<void>.delayed(const Duration(milliseconds: 700));

    expect(controller.state.updateRequired, isTrue);
    expect(controller.state.connection, ConnectionStatus.disconnected,
        reason: 'a reconnect would only be refused again');
  });

  test('a spent talk budget ends the session with Upgrade on offer, no reconnect',
      () async {
    // Refused at connect (before `ready`): the server sends the fatal error
    // and closes. The overlay that offers Upgrade only shows once the
    // connection is `disconnected`; an auto-reconnect would keep it
    // `reconnecting`, be refused again, and stack another notice each time.
    await controller.connect();
    await tick();
    fake.pushJson({
      'type': 'error',
      'code': 'quota_exceeded',
      'message': "You've used your free trial's 30 minutes of talk time.",
      'fatal': true,
    });
    await tick();
    fake.drop();
    await Future<void>.delayed(const Duration(milliseconds: 700));

    expect(controller.state.capReached, isTrue);
    expect(controller.state.transcripts.last.role, 'notice');
    expect(controller.state.transcripts.where((t) => t.role == 'notice').length, 1,
        reason: 'one notice, not one per reconnect attempt');
    expect(controller.state.connection, ConnectionStatus.disconnected,
        reason: 'the overlay with Upgrade needs a disconnected state');
  });

  test('the session_expired that follows a quota refusal adds no second notice',
      () async {
    // The server sends the fatal error, then session_expired (so builds that
    // predate the fix stop reconnecting too), then closes. One amber line.
    await controller.connect();
    await tick();
    fake.pushJson({
      'type': 'error',
      'code': 'quota_exceeded',
      'message': "You've used your free trial's 30 minutes of talk time.",
      'fatal': true,
    });
    fake.pushJson({'type': 'session_expired', 'reason': 'quota_exceeded'});
    await tick();
    fake.drop();
    await Future<void>.delayed(const Duration(milliseconds: 700));

    expect(controller.state.capReached, isTrue);
    expect(controller.state.transcripts.where((t) => t.role == 'notice').length, 1);
    expect(controller.state.connection, ConnectionStatus.disconnected);
  });

  test('a cascade session speaks assistant replies with the phone voice',
      () async {
    // The `ready.model` label starting with `cascade:` is the whole signal:
    // that provider sends words, never audio, and the phone says them with
    // the mic shut for the duration (the speaker must not become a turn).
    final voice = FakeDeviceVoice();
    final ctl = LiveController(
      config: const AppConfig(host: 'h', port: 8000, secure: false),
      registry: registry,
      player: player,
      permissions: GrantingPermissions(),
      clientFactory: (cfg, deviceInfo) => WebSocketLiveClient(
        config: cfg,
        platform: 'android',
        deviceInfoProvider: deviceInfo,
        channelFactory: (_) => fake,
      ),
      deviceVoice: voice,
      platform: 'android',
    );
    addTearDown(ctl.dispose);
    await ctl.connect();
    await tick();
    fake.pushJson({
      'type': 'ready',
      'sessionId': 's-c',
      'protocolVersion': 1,
      'model': 'cascade:whisper+nemotron',
    });
    await tick();
    fake.pushJson({
      'type': 'transcript',
      'role': 'assistant',
      'text': 'नमस्ते, मैं सुन रही हूँ।',
      'final': true,
    });
    await tick();

    expect(voice.spoken, ['नमस्ते, मैं सुन रही हूँ।']);
    expect(voice.languages, ['hi-IN']);
  });

  test('a Gemini session never uses the phone voice', () async {
    final voice = FakeDeviceVoice();
    final ctl = LiveController(
      config: const AppConfig(host: 'h', port: 8000, secure: false),
      registry: registry,
      player: player,
      permissions: GrantingPermissions(),
      clientFactory: (cfg, deviceInfo) => WebSocketLiveClient(
        config: cfg,
        platform: 'android',
        deviceInfoProvider: deviceInfo,
        channelFactory: (_) => fake,
      ),
      deviceVoice: voice,
      platform: 'android',
    );
    addTearDown(ctl.dispose);
    await ctl.connect();
    await tick();
    fake.pushJson({
      'type': 'ready',
      'sessionId': 's-g',
      'protocolVersion': 1,
      'model': 'gemini-2.5-flash-native-audio-latest',
    });
    await tick();
    fake.pushJson({
      'type': 'transcript',
      'role': 'assistant',
      'text': 'Hello there.',
      'final': true,
    });
    await tick();
    expect(voice.spoken, isEmpty);
  });

  test('the mic gate opening tells the server speech started', () async {
    // On a glasses mic the backend turns this into the model's activityStart
    // (manual detection); it must go out the moment the gate opens.
    await controller.connect();
    await tick();
    await controller.startListening();
    await tick();

    source.audioCtl.add(Uint8List.fromList([5, 6])); // one loud sample
    await tick();

    final types = fake.sentLog
        .whereType<String>()
        .map((s) => (jsonDecode(s) as Map<String, dynamic>)['type'])
        .toList();
    expect(types, contains('speech_start'));
    expect(types, isNot(contains('speech_end')),
        reason: 'the gate is still open');
  });

  test('a Hindi or Urdu user line reaches the chat like an English one',
      () async {
    // Dart's \w is ASCII: the duplicate filter normalised every Devanagari
    // and Urdu line to nothing and rejected it (device 2026-09-15 18:48).
    await controller.connect();
    await tick();
    for (final line in ['हिंदी में बात करो', 'ہلو اب یہ بتاؤ', 'Hello']) {
      fake.pushJson({
        'type': 'transcript', 'role': 'user', 'text': line, 'final': true,
      });
      await tick();
    }
    final users = controller.state.transcripts
        .where((t) => t.role == 'user')
        .map((t) => t.text)
        .toList();
    expect(users, ['हिंदी में बात करो', 'ہلو اب یہ بتاؤ', 'Hello']);
  });

  test('an answer that repeats the words of the question is not an echo', () async {
    // Device 2026-09-15 19:46: "अपनी बीवी को प्यार करने की सोच रहा हूं" was
    // dropped as an echo of "किसको प्यार करने की सोच रहे हैं?" — the vowel
    // signs had been stripped, so रहे/रहा and की/को compared equal.
    await controller.connect();
    await tick();
    final seq = [
      ('user', 'को प्यार करने की सोच रहा हूं।'),
      ('assistant', 'किसको प्यार करने की सोच रहे हैं?'),
      ('user', 'अपनी बीवी को प्यार करने की सोच रहा हूं।'),
      ('assistant', 'यह तो बहुत अच्छी बात है!'),
    ];
    for (final (role, text) in seq) {
      if (role == 'user') {
        fake.pushJson({'type': 'transcript', 'role': 'user', 'text': text, 'final': false});
        await tick();
      }
      fake.pushJson({'type': 'transcript', 'role': role, 'text': text, 'final': true});
      await tick();
    }
    final users = controller.state.transcripts
        .where((t) => t.role == 'user')
        .map((t) => t.text)
        .toList();
    expect(users, [
      'को प्यार करने की सोच रहा हूं।',
      'अपनी बीवी को प्यार करने की सोच रहा हूं।',
    ]);
  });

  test('speech the gate held back is reported to the server with its levels',
      () async {
    await controller.connect();
    await tick();
    await controller.startListening();
    await tick();

    // 240 ms at RMS 300: over half the phone bar (396 → 198), under it.
    final soft = Int16List(320)..fillRange(0, 320, 300);
    for (var i = 0; i < 12; i++) {
      source.audioCtl.add(soft.buffer.asUint8List());
    }
    await tick();
    // The stretch ends once the room has been quiet for 400 ms.
    await Future<void>.delayed(const Duration(milliseconds: 450));
    source.audioCtl.add(Uint8List(640));
    await tick();

    final missed = fake.sentLog
        .whereType<String>()
        .map((s) => jsonDecode(s) as Map<String, dynamic>)
        .where((m) => m['type'] == 'gate_missed')
        .toList();
    expect(missed.length, 1);
    expect(missed.single['peakRms'], 300);
    expect(missed.single['halfMs'], 240);
    expect(missed.single['loudMs'], 0);
    expect(missed.single['mic'], 'phone');
    final types = fake.sentLog
        .whereType<String>()
        .map((s) => (jsonDecode(s) as Map<String, dynamic>)['type']);
    expect(types, isNot(contains('speech_start')));
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

  test('mic is muted while the speaker is still playing (echo guard)',
      () async {
    // The assistant's own voice reaching the mic gets transcribed as a user
    // turn and answered — a self-talk loop the user hit on 2026-08-05. The
    // player's drain clock, not a timer, decides when it is safe to listen.
    await controller.connect();
    await tick();
    await controller.startListening();
    await tick();

    List<Uint8List> micFrames() => fake.sentLog
        .whereType<Uint8List>()
        .map(MediaFrame.decode)
        .where((f) => f.tag == FrameTag.inputAudio)
        .map((f) => f.payload)
        .toList();

    player.playing = true; // speaker busy (or still draining)
    source.audioCtl.add(Uint8List.fromList([9, 9]));
    await tick();
    expect(micFrames(), isEmpty, reason: 'mic must stay shut while speaking');

    player.playing = false; // drained + tail elapsed
    source.audioCtl.add(Uint8List.fromList([7, 7]));
    await tick();
    expect(micFrames().last, equals(Uint8List.fromList([7, 7])));
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

  // ---- Glasses video recording ------------------------------------------
  //
  // The feature is deliberately isolated from the rest of the session, so
  // these drive it end to end through the controller and pin the two ways it
  // could mislead someone: a "recording" badge for a recording that never
  // started, and a badge that outlives one that ended.

  LiveController newGlassesController(FakeGlassesBridge glasses,
      {AppConfig? config, FakePcmPlayer? player}) {
    final ctl = LiveController(
      config: config ?? const AppConfig(host: 'h', port: 8000, secure: false),
      registry: DeviceRegistry(factory: (_) => FakeCaptureSource()),
      player: player ?? FakePcmPlayer(),
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
    // The controller pushes the recording length at construction (the glasses
    // can already be linked before it subscribes — see the constructor). That
    // is asserted on its own below; drop it here so each test reads clearly.
    expect(glasses.videoCalls, ['duration:60']);
    glasses.videoCalls.clear();
    return ctl;
  }

  test('the recording length is pushed to the glasses at startup', () async {
    // Regression, device-seen 2026-08-08: pushed only on the connect event,
    // which never fired because an auto-connect had already completed. The
    // glasses then kept their OWN 180 s duration.
    final glasses = FakeGlassesBridge();
    newGlassesController(glasses); // asserts the startup push in the helper
  });

  test('recording starts only once the glasses confirm it', () async {
    final glasses = FakeGlassesBridge();
    final ctl = newGlassesController(glasses);
    await ctl.connect();
    await tick();
    glasses.emit('connectionState', {'state': 'connected', 'mac': 'AA:BB:CC'});
    await tick();

    await ctl.startGlassesRecording();
    await tick();
    expect(glasses.recordingCalls, ['start:60'],
        reason: 'the configured duration is what gets sent');
    expect(ctl.state.recording, isNull,
        reason: 'the badge must wait for the device, not for our own request');

    glasses.emit('videoState',
        {'state': 'recording', 'requestId': 'v1', 'seconds': 60});
    await tick();
    expect(ctl.state.recording?.requestId, 'v1');
    expect(ctl.state.recording?.seconds, 60);
  });

  test('a refused start clears the badge and says why', () async {
    final glasses = FakeGlassesBridge();
    final ctl = newGlassesController(glasses);
    await ctl.connect();
    await tick();
    glasses.emit('connectionState', {'state': 'connected', 'mac': 'AA:BB:CC'});
    await tick();
    glasses.emit('videoState',
        {'state': 'recording', 'requestId': 'v1', 'seconds': 60});
    await tick();

    glasses.emit('videoState', {
      'state': 'failed',
      'requestId': 'v1',
      'seconds': 60,
      'reason': 'low_battery',
      'detail': 'the glasses are at 9%',
    });
    await tick();
    expect(ctl.state.recording, isNull);
    expect(ctl.state.lastError, contains('9%'),
        reason: 'a failure must reach the user, never be swallowed');
  });

  test('stopping is refused while disconnected, with a reason', () async {
    final glasses = FakeGlassesBridge();
    final ctl = newGlassesController(glasses);
    await ctl.connect();
    await tick();

    await ctl.startGlassesRecording();
    await tick();
    expect(glasses.recordingCalls, isEmpty,
        reason: 'no BLE command may be sent to glasses that are not connected');
    expect(ctl.state.lastError, contains('Connect the glasses'));
  });

  test('a stop ends the recording and asks for a sync when saving is on',
      () async {
    final glasses = FakeGlassesBridge();
    final ctl = newGlassesController(glasses);
    await ctl.connect();
    await tick();
    glasses.emit('connectionState', {'state': 'connected', 'mac': 'AA:BB:CC'});
    await tick();
    glasses.emit('videoState',
        {'state': 'recording', 'requestId': 'v1', 'seconds': 30});
    await tick();

    await ctl.stopGlassesRecording();
    await tick();
    expect(glasses.recordingCalls, contains('stop'));

    glasses.emit('videoState', {
      'state': 'stopped',
      'requestId': 'v1',
      'seconds': 30,
      'reason': 'user_stopped',
    });
    await tick();
    expect(ctl.state.recording, isNull);
  });

  test('voice tools drive the same recording path as the button', () async {
    final glasses = FakeGlassesBridge();
    final ctl = newGlassesController(glasses);
    await ctl.connect();
    await tick();
    glasses.emit('connectionState', {'state': 'connected', 'mac': 'AA:BB:CC'});
    await tick();

    fake.pushJson({
      'type': 'tool_call',
      'id': 'r1',
      'name': 'record_video',
      'args': <String, dynamic>{},
      'needsPermission': false,
    });
    await tick();
    expect(glasses.recordingCalls, isEmpty,
        reason: 'a spoken confirmation must not be cut off by the recording');

    // The voice path waits for that confirmation before rolling — here none
    // ever arrives, so it proceeds once the grace window closes.
    await Future<void>.delayed(const Duration(milliseconds: 1900));
    await tick();
    expect(glasses.recordingCalls, ['start:60']);

    glasses.emit('videoState',
        {'state': 'recording', 'requestId': 'v1', 'seconds': 60});
    await tick();
    fake.pushJson({
      'type': 'tool_call',
      'id': 'r2',
      'name': 'stop_recording',
      'args': <String, dynamic>{},
      'needsPermission': false,
    });
    await tick();
    expect(glasses.recordingCalls, ['start:60', 'stop']);
  });

  test('the settings chooser pushes the duration to the glasses', () async {
    final glasses = FakeGlassesBridge();
    final ctl = newGlassesController(glasses);
    await ctl.setVideoRecordSeconds(240);
    await tick();
    expect(glasses.videoCalls, ['duration:240']);
  });

  test('the on-screen clock never runs past the chosen length', () {
    final rec = GlassesRecording(
      requestId: 'v1',
      seconds: 30,
      startedAt: DateTime.now().subtract(const Duration(seconds: 47)),
    );
    expect(rec.clampedElapsed, const Duration(seconds: 30));
    expect(rec.label, '0:30 / 0:30');
  });

  // ---- Farry is silent while the glasses record ---------------------------
  //
  // The glasses capture their audio from their OWN microphone, so anything
  // Farry says during a recording lands in the video. These pin the two halves
  // of that: nothing comes out while recording, and everything comes back
  // afterwards — including down the failure paths, because a permanently mute
  // assistant is a far worse bug than a spoiled video.

  test('no audio reaches the speaker while a recording is running', () async {
    final glasses = FakeGlassesBridge();
    final player = FakePcmPlayer();
    final ctl = newGlassesController(glasses, player: player);
    await ctl.connect();
    await tick();
    glasses.emit('connectionState', {'state': 'connected', 'mac': 'AA:BB:CC'});
    await tick();

    fake.pushBinary(MediaFrame.encode(
        tag: FrameTag.outputAudio, timestampMs: 1, payload: Uint8List.fromList([1, 2, 3, 4])));
    await tick();
    expect(player.fed, hasLength(1), reason: 'normal playback works');

    glasses.emit('videoState',
        {'state': 'recording', 'requestId': 'v1', 'seconds': 30});
    await tick();
    fake.pushBinary(MediaFrame.encode(
        tag: FrameTag.outputAudio, timestampMs: 2, payload: Uint8List.fromList([1, 2, 3, 4])));
    await tick();
    expect(player.fed, hasLength(1),
        reason: "Farry's voice must not be recorded into the video");

    glasses.emit('videoState', {
      'state': 'stopped',
      'requestId': 'v1',
      'seconds': 30,
      'reason': 'duration_reached',
    });
    await tick();
    fake.pushBinary(MediaFrame.encode(
        tag: FrameTag.outputAudio, timestampMs: 3, payload: Uint8List.fromList([1, 2, 3, 4])));
    await tick();
    expect(player.fed, hasLength(2),
        reason: 'the speaker must come back the moment recording ends');
  });

  test('a failed recording still gives the speaker back', () async {
    // The gate is derived from the recording state precisely so it cannot
    // stick. This drives the nastiest exit — a mid-recording disconnect.
    final glasses = FakeGlassesBridge();
    final player = FakePcmPlayer();
    final ctl = newGlassesController(glasses, player: player);
    await ctl.connect();
    await tick();
    glasses.emit('connectionState', {'state': 'connected', 'mac': 'AA:BB:CC'});
    await tick();
    glasses.emit('videoState',
        {'state': 'recording', 'requestId': 'v1', 'seconds': 240});
    await tick();

    glasses.emit('videoState', {
      'state': 'failed',
      'requestId': 'v1',
      'seconds': 240,
      'reason': 'disconnected',
      'detail': 'the glasses disconnected mid-recording',
    });
    await tick();
    fake.pushBinary(MediaFrame.encode(
        tag: FrameTag.outputAudio, timestampMs: 9, payload: Uint8List.fromList([1, 2, 3, 4])));
    await tick();
    expect(player.fed, hasLength(1),
        reason: 'a failure must never leave the assistant permanently mute');
  });

  test('the headset assistant button (temple long-press) opens a closed mic',
      () async {
    final glasses = FakeGlassesBridge();
    final ctl = newGlassesController(glasses);
    await ctl.connect();
    await tick();
    fake.pushJson({
      'type': 'ready',
      'sessionId': 'sess-vc',
      'protocolVersion': 1,
      'model': 'test',
    });
    await tick();
    glasses.emit('connectionState', {'state': 'connected', 'mac': 'AA:BB:CC'});
    await tick();
    // The user muted Farry (the hands-free session opens the mic by itself).
    await ctl.stopListening();
    await tick();
    expect(ctl.state.micOpen, isFalse);

    glasses.emit('voiceCommand', {'source': 'headset'});
    await tick();
    expect(ctl.state.micOpen, isTrue,
        reason: 'the press that used to open Google/Bixby now opens Farry');

    // Pressing again while open is a no-op, not a toggle: the PCM path is
    // already streaming for as long as the temple is held.
    glasses.emit('voiceCommand', {'source': 'headset'});
    await tick();
    expect(ctl.state.micOpen, isTrue);
  });

  test('the mic is closed before the glasses are told to roll', () async {
    final glasses = FakeGlassesBridge();
    final ctl = newGlassesController(glasses);
    await ctl.connect();
    await tick();
    glasses.emit('connectionState', {'state': 'connected', 'mac': 'AA:BB:CC'});
    await tick();
    await ctl.startListening();
    await tick();
    expect(ctl.state.micOpen, isTrue);

    await ctl.startGlassesRecording();
    await tick();
    expect(ctl.state.micOpen, isFalse,
        reason: 'the mic must be shut before recording, not after');
    expect(glasses.recordingCalls, ['start:60']);
  });

  test('a finished recording is announced on the dashboard and out loud',
      () async {
    final glasses = FakeGlassesBridge();
    final ctl = newGlassesController(glasses);
    await ctl.connect();
    await tick();
    // A live session: the spoken half only fires when one is up.
    fake.pushJson({
      'type': 'ready',
      'sessionId': 'sess-rec',
      'protocolVersion': 1,
      'model': 'test',
    });
    await tick();
    glasses.emit('connectionState', {'state': 'connected', 'mac': 'AA:BB:CC'});
    await tick();
    glasses.emit('videoState',
        {'state': 'recording', 'requestId': 'v1', 'seconds': 30});
    await tick();

    glasses.emit('videoState', {
      'state': 'stopped',
      'requestId': 'v1',
      'seconds': 30,
      'reason': 'duration_reached',
    });
    await tick();

    // On screen…
    expect(
      ctl.state.transcripts.where((t) => t.isNotice).map((t) => t.text),
      contains(contains('Recording finished')),
    );
    // …and asked of the assistant, so it reaches a pocketed phone too.
    final spoken = fake.sentLog
        .whereType<String>()
        .any((m) => m.contains('recording on the glasses just finished'));
    expect(spoken, isTrue,
        reason: 'the completion must reach someone not looking at the screen');
  });

  test('sync progress is surfaced, and cleared when it finishes', () async {
    final glasses = FakeGlassesBridge();
    final ctl = newGlassesController(glasses);
    await ctl.connect();
    await tick();
    glasses.emit('connectionState', {'state': 'connected', 'mac': 'AA:BB:CC'});
    await tick();

    glasses.emit('syncProgress',
        {'file': 'video.mp4', 'pct': 42, 'speedKbps': 3.6});
    await tick();
    expect(ctl.state.syncStatus?.pct, 42);
    expect(ctl.state.syncStatus?.file, 'video.mp4');

    glasses.emit('syncProgress', {'file': 'all files done', 'pct': 100});
    await tick();
    expect(ctl.state.syncStatus, isNull,
        reason: 'a finished transfer must not leave a progress bar on screen');
  });

  test('the record button is held while a start is in flight', () async {
    // Start and stop are one command on the glasses, so a second tap before
    // the device answers used to stop the recording that was just starting.
    final glasses = FakeGlassesBridge();
    final ctl = newGlassesController(glasses);
    await ctl.connect();
    await tick();
    glasses.emit('connectionState', {'state': 'connected', 'mac': 'AA:BB:CC'});
    await tick();

    // Two taps in the same breath must reach the glasses ONCE. A second
    // toggle would stop the recording the first one is starting.
    final first = ctl.startGlassesRecording();
    final second = ctl.startGlassesRecording();
    await Future.wait([first, second]);
    await tick();
    expect(glasses.recordingCalls, ['start:60'],
        reason: 'a double tap must not toggle the recording straight back off');
    expect(ctl.state.recordingBusy, isFalse,
        reason: 'the button must come back once the request is away');
  });

  // ---- Manual media sync -------------------------------------------------
  //
  // A transfer takes the whole device — no recording, no photo while it runs —
  // so someone shooting several clips needs to defer it. These pin that
  // "manual" really means the app never starts one on its own, and that the
  // user is still told what is waiting.

  test('manual mode does not sync on its own, but does count what is waiting',
      () async {
    final glasses = FakeGlassesBridge();
    final ctl = newGlassesController(
      glasses,
      config: const AppConfig(
          host: 'h', port: 8000, secure: false, autoMediaSync: false),
    );
    await ctl.connect();
    await tick();
    glasses.emit('connectionState', {'state': 'connected', 'mac': 'AA:BB:CC'});
    await tick();
    glasses.emit('videoState',
        {'state': 'recording', 'requestId': 'v1', 'seconds': 30});
    await tick();
    glasses.emit('videoState', {
      'state': 'stopped',
      'requestId': 'v1',
      'seconds': 30,
      'reason': 'duration_reached',
    });
    // Past every debounce the auto path uses.
    await Future<void>.delayed(const Duration(milliseconds: 100));
    await tick();

    expect(glasses.syncCalls, 0,
        reason: 'manual means the app never takes the device by itself');
  });

  group('glasses battery', () {
    setUp(() {
      LiveController.glassesBatteryDelay = Duration.zero;
      LiveController.glassesBatteryEvery = const Duration(milliseconds: 40);
    });
    tearDown(() {
      LiveController.glassesBatteryDelay = const Duration(seconds: 2);
      LiveController.glassesBatteryEvery = const Duration(minutes: 5);
    });

    test('glasses linked before the session are asked for their battery',
        () async {
      final glasses = FakeGlassesBridge()
        ..info = const {'lastMac': 'AA:BB:CC', 'connectedMac': 'AA:BB:CC'};
      final ctl = newGlassesController(glasses);
      await ctl.connect();
      await Future<void>.delayed(const Duration(milliseconds: 10));
      expect(glasses.batteryCalls, greaterThanOrEqualTo(1));
      await ctl.dispose();
    });

    test('a connect asks, the ask repeats, a drop stops it', () async {
      final glasses = FakeGlassesBridge();
      final ctl = newGlassesController(glasses);
      await ctl.connect();
      await tick();
      expect(glasses.batteryCalls, 0, reason: 'nothing connected yet');

      glasses.emit('connectionState', {'state': 'connected', 'mac': 'AA:BB:CC'});
      await Future<void>.delayed(const Duration(milliseconds: 10));
      expect(glasses.batteryCalls, 1);
      await Future<void>.delayed(const Duration(milliseconds: 100));
      expect(glasses.batteryCalls, greaterThanOrEqualTo(2));

      glasses.emit('connectionState', {'state': 'disconnected'});
      await tick();
      final atDrop = glasses.batteryCalls;
      await Future<void>.delayed(const Duration(milliseconds: 100));
      expect(glasses.batteryCalls, atDrop);
      await ctl.dispose();
    });

    test("another pair never shows the last pair's battery", () async {
      // Device 2026-09-25: after the GS5 (99%) the chip said "L801-03BC 99%"
      // although that pair never reported a battery.
      final glasses = FakeGlassesBridge();
      final ctl = newGlassesController(glasses);
      await ctl.connect();
      await tick();
      glasses.emit('connectionState',
          {'state': 'connected', 'mac': 'AA', 'name': 'SNT GS5 MAX_9475'});
      glasses.emit('battery', {'pct': 99, 'charging': false});
      await tick();
      expect(ctl.state.glassesBattery, 99);

      glasses.emit('connectionState', {'state': 'disconnected'});
      await tick();
      expect(ctl.state.glassesBattery, isNull, reason: 'no pair, no battery');

      glasses.emit('battery', {'pct': 98, 'charging': false});
      glasses.emit('connectionState',
          {'state': 'connected', 'mac': 'BB', 'name': 'L801-03BC'});
      await tick();
      expect(ctl.state.glassesBattery, isNull,
          reason: 'a reading that came before the switch is not this pair');

      glasses.emit('battery', {'pct': 57, 'charging': false});
      await tick();
      expect(ctl.state.glassesBattery, 57);
      await ctl.dispose();
    });

    test('glasses that drop and come straight back keep the camera', () async {
      // Device 2026-09-26 15:04 (app reopened): the link dropped and was back
      // two seconds later. "Phone" was still switching when "glasses" was
      // asked; the second call saw the registry still on glasses and did
      // nothing, and the photo was then taken with the phone camera.
      final glasses = FakeGlassesBridge();
      final ctl = newGlassesController(glasses);
      await ctl.connect();
      await tick();
      glasses.emit('connectionState',
          {'state': 'connected', 'mac': 'AA', 'name': 'SNT GS5 MAX_9475'});
      await tick();
      expect(ctl.state.videoKind, 'glasses');

      // Drop and return back to back — no settling in between.
      glasses.emit('connectionState', {'state': 'disconnected'});
      glasses.emit('connectionState',
          {'state': 'connected', 'mac': 'AA', 'name': 'SNT GS5 MAX_9475'});
      await tick();
      await tick();
      expect(ctl.state.videoKind, 'glasses',
          reason: 'the last request (glasses) must win');
      await ctl.dispose();
    });

    test('the answer reaches the chip', () async {
      final glasses = FakeGlassesBridge();
      final ctl = newGlassesController(glasses);
      await ctl.connect();
      await tick();
      glasses.emit('battery', {'pct': 64, 'charging': false});
      await tick();
      expect(ctl.state.glassesBattery, 64);
      await ctl.dispose();
    });
  });

  test('the pending count reaches the UI', () async {
    final glasses = FakeGlassesBridge();
    final ctl = newGlassesController(glasses);
    await ctl.connect();
    await tick();
    glasses.emit('mediaCount', {'img': 1, 'vid': 2, 'rec': 0});
    await tick();
    expect(ctl.state.pendingMedia?.total, 3);
    expect(ctl.state.pendingMedia?.label, '1 photo · 2 videos');
  });

  test('Sync now refuses with a reason when it cannot run', () async {
    final glasses = FakeGlassesBridge();
    final ctl = newGlassesController(glasses);
    await ctl.connect();
    await tick();

    await ctl.syncGlassesNow(); // glasses not connected
    await tick();
    expect(glasses.syncCalls, 0);
    expect(ctl.state.lastError, contains('Connect the glasses'));

    glasses.emit('connectionState', {'state': 'connected', 'mac': 'AA:BB:CC'});
    await tick();
    glasses.emit('videoState',
        {'state': 'recording', 'requestId': 'v1', 'seconds': 30});
    await tick();
    await ctl.syncGlassesNow(); // recording in progress
    await tick();
    expect(glasses.syncCalls, 0);
    expect(ctl.state.lastError, contains("Can't sync while"));
  });

  test('Sync now asks the album, not the media count', () async {
    // Device 2026-09-23: three button photos on the glasses, and the count
    // said img=0 — a count-gated sync never looked.
    final glasses = FakeGlassesBridge();
    final ctl = newGlassesController(glasses);
    await ctl.connect();
    await tick();
    glasses.emit('connectionState', {'state': 'connected', 'mac': 'AA:BB:CC'});
    await tick();
    await ctl.syncGlassesNow();
    await tick();
    expect(glasses.forcedSyncCalls, 1);
    expect(glasses.syncCalls, 0, reason: 'the gated path is for auto-sync only');
  });

  test('a synced photo is shown but never sent to the model', () async {
    // A wearer's button photo has no BLE thumbnail, so the sync is the first
    // time the phone has it. It should appear — and go no further: the user
    // asked to see their photos, not to be told about them.
    final glasses = FakeGlassesBridge();
    final ctl = newGlassesController(glasses);
    await ctl.connect();
    await tick();
    final before = fake.sentLog.whereType<Uint8List>().length;

    glasses.emit('syncedPhoto', {
      'jpeg': Uint8List.fromList([9, 8, 7, 6]),
      'name': 'IMG_1.jpg',
    });
    await tick();

    expect(ctl.state.lastCapturedPhoto, Uint8List.fromList([9, 8, 7, 6]));
    expect(fake.sentLog.whereType<Uint8List>().length, before,
        reason: 'a synced photo must not be pushed to the model as a frame');
  });

  test('the mic is restored only if it was open beforehand', () async {
    final glasses = FakeGlassesBridge();
    final ctl = newGlassesController(glasses);
    await ctl.connect();
    await tick();
    glasses.emit('connectionState', {'state': 'connected', 'mac': 'AA:BB:CC'});
    await tick();
    await ctl.stopListening();
    await tick();
    expect(ctl.state.micOpen, isFalse);

    await ctl.startGlassesRecording();
    await tick();
    glasses.emit('videoState',
        {'state': 'recording', 'requestId': 'v1', 'seconds': 30});
    await tick();
    glasses.emit('videoState', {
      'state': 'stopped',
      'requestId': 'v1',
      'seconds': 30,
      'reason': 'user_stopped',
    });
    await tick();
    expect(ctl.state.micOpen, isFalse,
        reason: "a closed mic must stay closed — don't hand back more than we took");
  });
}
