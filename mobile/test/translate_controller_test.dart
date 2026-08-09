import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:farryon/capture/device_registry.dart';
import 'package:farryon/core/config.dart';
import 'package:farryon/data/live_client.dart';
import 'package:farryon/features/translate/translate_controller.dart';
import 'package:farryon/features/translate/translate_state.dart';
import 'package:farryon/protocol/frames.dart';
import 'package:farryon/protocol/messages.dart';
import 'package:farryon/protocol/protocol.dart';
import 'package:farryon/features/glasses_lab/bridge/glasses_channel.dart';
import 'package:farryon/state/permissions.dart';
import 'package:flutter_test/flutter_test.dart';

import 'live_client_test.dart' show FakeChannel;
import 'live_controller_test.dart' show FakeCaptureSource, FakePcmPlayer;

class _GrantingPermissions implements PermissionsService {
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
}

/// Only the event stream is used — the translate session never drives the
/// glasses, it just needs to hear them drop.
class _FakeGlasses implements GlassesBridgeApi {
  final controller = StreamController<GlassesLabEvent>.broadcast();

  void emit(String type, Map<String, Object?> data) =>
      controller.add(GlassesLabEvent(type: type, data: data));

  @override
  Stream<GlassesLabEvent> events() => controller.stream;

  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnsupportedError('the translate session must not drive the glasses');
}

class _DenyingPermissions extends _GrantingPermissions {
  @override
  Future<PermissionOutcome> requestMicrophone() async =>
      PermissionOutcome.denied;
}

void main() {
  late FakeChannel fake;
  late FakeCaptureSource source;
  late FakePcmPlayer player;
  late DeviceRegistry registry;
  late TranslateController controller;
  late List<Map<String, dynamic>> helloFrames;

  /// The client the controller opens, built over [fake] so tests can push
  /// server messages in and read what the handshake sent out.
  WebSocketLiveClient factory(
    AppConfig cfg,
    TranslateSessionConfig tx,
    DeviceInfo Function() deviceInfo,
  ) =>
      WebSocketLiveClient(
        config: cfg,
        platform: 'android',
        deviceInfoProvider: deviceInfo,
        translate: tx,
        channelFactory: (_) => fake,
      );

  TranslateController build({PermissionsService? permissions}) =>
      TranslateController(
        config: const AppConfig(host: 'h', port: 8000, secure: false),
        registry: registry,
        player: player,
        permissions: permissions ?? _GrantingPermissions(),
        clientFactory: factory,
      );

  setUp(() {
    fake = FakeChannel();
    source = FakeCaptureSource();
    player = FakePcmPlayer();
    registry = DeviceRegistry(factory: (_) => source);
    controller = build();
    helloFrames = [];
  });

  tearDown(() async {
    await controller.dispose();
  });

  Future<void> pump([int times = 3]) async {
    for (var i = 0; i < times; i++) {
      await Future<void>.delayed(Duration.zero);
    }
  }

  List<Map<String, dynamic>> sentJson() => [
        for (final s in fake.sentLog)
          if (s is String) jsonDecode(s) as Map<String, dynamic>,
      ];

  /// Bring a session up to `listening`.
  Future<void> connect({String target = 'hi'}) async {
    await controller.setTargetLanguage(target);
    await controller.start();
    await pump();
    helloFrames = sentJson().where((m) => m['type'] == 'hello').toList();
    fake.pushJson({
      'type': 'ready',
      'sessionId': 's1',
      'protocolVersion': kProtocolVersion,
      'model': 'mock-translate-1',
      'mode': 'translate',
      'targetLanguage': target,
    });
    await pump();
  }

  void hear(String text,
          {required String lang, bool isFinal = true}) =>
      fake.pushJson({
        'type': 'transcript',
        'role': 'user',
        'text': text,
        'final': isFinal,
        'lang': lang,
      });

  void translated(String text, {required String lang}) => fake.pushJson({
        'type': 'transcript',
        'role': 'assistant',
        'text': text,
        'final': true,
        'lang': lang,
      });

  group('the handshake', () {
    test('declares translate mode and the target language', () async {
      await connect(target: 'hi');
      expect(helloFrames, hasLength(1));
      expect(helloFrames.first['mode'], 'translate');
      expect(helloFrames.first['translate']['targetLanguage'], 'hi');
      // The SOURCE language is never sent — the model detects it.
      expect(helloFrames.first['translate'].containsKey('sourceLanguage'),
          isFalse);
    });

    test('sends no mailbox, web-search or language config', () async {
      await connect();
      final hello = helloFrames.first;
      // These configure tools and a system prompt. A translate session has
      // neither, so sending them would ship credentials to a session that
      // could never use them.
      expect(hello.containsKey('emails'), isFalse);
      expect(hello.containsKey('email'), isFalse);
      expect(hello.containsKey('webSearch'), isFalse);
      expect(hello.containsKey('languages'), isFalse);
    });

    test('opens the microphone only once the server is ready', () async {
      await controller.setTargetLanguage('hi');
      await controller.start();
      await pump();
      expect(source.audioStarted, isFalse,
          reason: 'frames sent before ready are dropped by the client');

      fake.pushJson({
        'type': 'ready',
        'sessionId': 's1',
        'protocolVersion': kProtocolVersion,
        'mode': 'translate',
      });
      await pump();
      expect(source.audioStarted, isTrue);
      expect(controller.state.status, TranslateStatus.listening);
    });
  });

  group('transcripts', () {
    test('a partial firms up into a final without adding a turn', () async {
      await connect(target: 'hi');
      hear('We will start', lang: 'en', isFinal: false);
      await pump();
      expect(controller.state.turns, hasLength(1));
      expect(controller.state.turns.single.heardFinal, isFalse);

      hear('We will start with the budget.', lang: 'en');
      await pump();
      expect(controller.state.turns, hasLength(1));
      expect(controller.state.turns.single.heardFinal, isTrue);
      expect(controller.state.turns.single.heard,
          'We will start with the budget.');
    });

    test('the translation attaches to the utterance it belongs to', () async {
      await connect(target: 'hi');
      hear('We will start with the budget.', lang: 'en');
      translated('हम बजट से शुरू करेंगे।', lang: 'hi');
      await pump();
      final turn = controller.state.turns.single;
      expect(turn.translated, 'हम बजट से शुरू करेंगे।');
      expect(turn.isComplete, isTrue);
    });

    test('a second utterance starts a new turn', () async {
      await connect(target: 'hi');
      hear('One.', lang: 'en');
      translated('एक।', lang: 'hi');
      hear('Two.', lang: 'en');
      await pump();
      expect(controller.state.turns, hasLength(2));
      expect(controller.state.turns.first.translated, 'एक।');
    });
  });

  group('speech already in the target language', () {
    test('is marked as such the moment it is heard', () async {
      // The model stays silent here (echoTargetLanguage: false). If the UI
      // waited for a translation that never comes, the feature would look
      // broken — so the verdict is reached from the detected language alone.
      await connect(target: 'hi');
      hear('ठीक है, मैं कल भेज दूँगा।', lang: 'hi');
      await pump();
      final turn = controller.state.turns.single;
      expect(turn.sameLanguage, isTrue);
      expect(turn.translated, isEmpty);
      expect(turn.isComplete, isTrue,
          reason: 'nothing more is coming, so the turn must not look pending');
    });

    test('is cleared if the model does speak after all', () async {
      // echoTargetLanguage can be turned on, and a detected language can be
      // wrong. Real audio must win over the prediction.
      await connect(target: 'hi');
      hear('ठीक है।', lang: 'hi');
      await pump();
      expect(controller.state.turns.single.sameLanguage, isTrue);

      translated('ठीक है।', lang: 'hi');
      await pump();
      expect(controller.state.turns.single.sameLanguage, isFalse);
      expect(controller.state.turns.single.translated, 'ठीक है।');
    });
  });

  group('audio', () {
    test('every mic chunk goes up — no gate, no echo guard', () async {
      await connect();
      // The assistant mutes its mic while the speaker is busy. A translator
      // must not: the room keeps talking over the translation.
      player.playing = true;
      source.audioCtl.add(Uint8List.fromList([1, 2, 3, 4]));
      source.audioCtl.add(Uint8List.fromList([5, 6, 7, 8]));
      await pump();
      final audioFrames = fake.sentLog.whereType<Uint8List>().where((b) {
        final f = MediaFrame.decode(b);
        return f.tag == FrameTag.inputAudio;
      });
      expect(audioFrames, hasLength(2));
    });

    test('translated audio plays, unless captions-only is on', () async {
      await connect();
      fake.pushBinary(MediaFrame.encode(
          tag: FrameTag.outputAudio,
          timestampMs: 1,
          payload: Uint8List.fromList([9, 9, 9, 9])));
      await pump();
      expect(player.fed, hasLength(1));

      controller.setCaptionsOnly(true);
      fake.pushBinary(MediaFrame.encode(
          tag: FrameTag.outputAudio,
          timestampMs: 2,
          payload: Uint8List.fromList([8, 8, 8, 8])));
      await pump();
      expect(player.fed, hasLength(1), reason: 'captions-only must not speak');
    });

    test('stopping releases the microphone', () async {
      await connect();
      expect(source.audioStarted, isTrue);
      await controller.stop();
      await pump();
      expect(source.audioStarted, isFalse);
      expect(controller.state.status, TranslateStatus.stopped);
    });
  });

  group('refusals', () {
    test('a denied microphone stops before any socket is opened', () async {
      await controller.dispose();
      controller = build(permissions: _DenyingPermissions());
      final ok = await controller.start();
      expect(ok, isFalse);
      expect(controller.state.status, TranslateStatus.idle);
      expect(controller.state.error, isNotNull);
      expect(fake.sentLog, isEmpty);
    });

    test('an agent session is refused rather than shown as translation',
        () async {
      // A server without translate support would hand back the assistant.
      // Presenting its answers as translations is the worst possible failure.
      await controller.setTargetLanguage('hi');
      await controller.start();
      await pump();
      fake.pushJson({
        'type': 'ready',
        'sessionId': 's1',
        'protocolVersion': kProtocolVersion,
        'mode': 'agent',
      });
      await pump();
      expect(controller.state.error, isNotNull);
      expect(controller.state.status, isNot(TranslateStatus.listening));
    });
  });

  group('the microphone going away mid-session', () {
    test('a stream that ends is reported, not ignored', () async {
      // This is the shape a phone call takes: Android hands the mic to the
      // dialer and the capture stream simply stops. A screen that keeps saying
      // "Listening" to a mic it no longer has is the worst outcome.
      await connect();
      expect(controller.state.status, TranslateStatus.listening);

      await source.audioCtl.close();
      await pump(5);

      expect(controller.state.error, contains('microphone stopped'));
      expect(controller.state.isRunning, isFalse);
    });

    test('a stream that errors is reported too', () async {
      await connect();
      source.audioCtl.addError(StateError('mic seized'));
      await pump(5);
      expect(controller.state.error, isNotNull);
      expect(controller.state.isRunning, isFalse);
    });
  });

  group('quota messages', () {
    test('a warning is a notice, not an error', () async {
      // "10 minutes left" painted in failure-red teaches people to skip both
      // that and the message that actually ends the session.
      await connect();
      fake.pushJson({
        'type': 'error',
        'code': 'quota_warning',
        'message': 'About 5 minutes of translation left today.',
        'fatal': false,
      });
      await pump();
      expect(controller.state.notice, contains('5 minutes'));
      expect(controller.state.error, isNull);
      expect(controller.state.isRunning, isTrue,
          reason: 'a warning must not end the session');
    });

    test('running out ends the session with the reason on screen', () async {
      await connect();
      fake.pushJson({
        'type': 'error',
        'code': 'quota_exceeded',
        'message': "You've used today's 5 minutes of live translation.",
        'fatal': true,
      });
      await pump(4);
      expect(controller.state.error, contains('5 minutes'));
      expect(controller.state.isRunning, isFalse);
      expect(source.audioStarted, isFalse, reason: 'the mic must be released');
    });
  });

  group('the glasses dropping while they are the microphone', () {
    late _FakeGlasses glasses;

    setUp(() {
      glasses = _FakeGlasses();
      registry.setAudioKind(CaptureDeviceKind.glasses);
      controller = TranslateController(
        config: const AppConfig(host: 'h', port: 8000, secure: false),
        registry: registry,
        player: player,
        permissions: _GrantingPermissions(),
        glasses: glasses,
        clientFactory: factory,
      );
    });

    tearDown(() async {
      await glasses.controller.close();
    });

    test('falls back to the phone instead of going deaf', () async {
      await connect();
      expect(registry.audioKind, CaptureDeviceKind.glasses);

      glasses.emit('connectionState', {'state': 'disconnected'});
      await pump(5);

      expect(registry.audioKind, CaptureDeviceKind.phone);
      expect(controller.state.notice, contains('phone'));
      expect(controller.state.isRunning, isTrue,
          reason: 'the conversation is still happening — keep translating');
    });

    test('a glasses drop while the phone is the mic changes nothing', () async {
      registry.setAudioKind(CaptureDeviceKind.phone);
      await connect();
      glasses.emit('connectionState', {'state': 'disconnected'});
      await pump(3);
      expect(controller.state.notice, isNull);
      expect(controller.state.isRunning, isTrue);
    });
  });
}
