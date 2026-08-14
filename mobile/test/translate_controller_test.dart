import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:farryon/capture/device_registry.dart';
import 'package:farryon/core/config.dart';
import 'package:farryon/data/live_client.dart';
import 'package:farryon/features/glasses_lab/bridge/glasses_channel.dart';
import 'package:farryon/features/translate/translate_controller.dart';
import 'package:farryon/features/translate/translate_state.dart';
import 'package:farryon/playback/device_voice.dart';
import 'package:farryon/playback/voice_audio_mode.dart';
import 'package:farryon/protocol/frames.dart';
import 'package:farryon/protocol/messages.dart';
import 'package:farryon/protocol/protocol.dart';
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

/// Stands in for Android's text-to-speech.
///
/// `speaking` is the question the echo guard actually asks. The real class
/// answers it from a count of utterances in flight; here it is set by hand so
/// a test can put the phone mid-sentence without waiting for one.
class _FakeVoice implements DeviceVoice {
  bool speaking = false;
  final List<String> said = <String>[];

  @override
  bool isSpeakingWithin(Duration tail) => speaking;

  @override
  Future<bool> speak(String text, String language) async {
    said.add(text);
    return true;
  }

  @override
  Future<void> stop() async => speaking = false;

  @override
  bool cannotSpeak(String language) => false;

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

/// Stands in for the platform audio-route switch. `route` is what the OS
/// would answer: `applied` (voice path taken, echo canceller has its
/// reference), `skipped_external_route` (already on a headset), or
/// `unavailable`.
class _FakeVoiceAudioMode implements VoiceAudioMode {
  _FakeVoiceAudioMode(this.route);
  final String route;
  int enters = 0;
  int exits = 0;

  @override
  Future<String> enter() async {
    enters++;
    return route;
  }

  @override
  Future<void> exit() async => exits++;

  @override
  bool get isActive => route == 'applied';

  @override
  dynamic noSuchMethod(Invocation invocation) => null;
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
  late _FakeVoice voice;
  late DeviceRegistry registry;
  late TranslateController controller;
  late List<Map<String, dynamic>> helloFrames;

  /// The client the controller opens, built over [fake] so tests can push
  /// server messages in and read what the handshake sent out.
  /// The URI the client actually dialled, so a test can check which token
  /// went out rather than which one was stored.
  Uri? lastUri;

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
        // A fresh channel per connect, like the real thing: a Dart stream
        // can only be listened to once, so reusing one made a reconnect throw
        // inside the test rather than in the code under test.
        channelFactory: (uri) {
          lastUri = uri;
          return fake = FakeChannel();
        },
      );

  TranslateController build({
    PermissionsService? permissions,
    VoiceAudioMode? voiceAudioMode,
    DeviceVoice? deviceVoice,
  }) =>
      TranslateController(
        config: const AppConfig(host: 'h', port: 8000, secure: false),
        registry: registry,
        player: player,
        permissions: permissions ?? _GrantingPermissions(),
        // Default: the platform could not switch paths, so the echo canceller
        // has no reference and the hold is the only defence left.
        voiceAudioMode: voiceAudioMode ?? _FakeVoiceAudioMode('unavailable'),
        deviceVoice: deviceVoice ?? voice,
        clientFactory: factory,
      );

  setUp(() {
    lastUri = null;
    fake = FakeChannel();
    source = FakeCaptureSource();
    player = FakePcmPlayer();
    voice = _FakeVoice();
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
  ///
  /// Primes the glasses as connected: live translation refuses to start
  /// without them, because the translation has to play somewhere the listening
  /// microphone cannot hear it.
  Future<void> connect({String target = 'hi'}) async {
    controller.primeFromConfig(
      const AppConfig(host: 'h', port: 8000, secure: false),
      glassesConnected: true,
    );
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
      controller.primeFromConfig(
        const AppConfig(host: 'h', port: 8000, secure: false),
        glassesConnected: true,
      );
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
    test('every mic chunk goes up — no energy gate', () async {
      await connect();
      // No GATE: quiet speech from across a room is exactly who a translator
      // exists to hear, so nothing is judged on loudness. (The echo guard is a
      // separate thing and has its own group below — it withholds only while
      // OUR audio is playing, which device testing proved is not optional.)
      player.playing = false;
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
      controller.primeFromConfig(
        const AppConfig(host: 'h', port: 8000, secure: false),
        glassesConnected: true,
      );
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

  group('the glasses', () {
    late _FakeGlasses glasses;

    setUp(() async {
      glasses = _FakeGlasses();
      await controller.dispose();
      controller = TranslateController(
        config: const AppConfig(host: 'h', port: 8000, secure: false),
        registry: registry,
        player: player,
        permissions: _GrantingPermissions(),
        glasses: glasses,
        voiceAudioMode: _FakeVoiceAudioMode('skipped_external_route'),
        clientFactory: factory,
        // The real window is thirty seconds. No test should sit through it.
        glassesGraceWindow: const Duration(milliseconds: 10),
      );
    });

    tearDown(() async {
      await glasses.controller.close();
    });

    test('without them it refuses to start, and says why', () async {
      // Not an arbitrary lock. On the phone's speaker the translation loops
      // back into the microphone listening to the room.
      controller.primeFromConfig(
        const AppConfig(host: 'h', port: 8000, secure: false),
        glassesConnected: false,
      );
      final ok = await controller.start();

      expect(ok, isFalse);
      expect(controller.state.error, contains('glasses'));
      expect(controller.state.error, contains('loops'),
          reason: 'saying no without the reason reads as an arbitrary lock');
      expect(fake.sentLog, isEmpty, reason: 'no socket should be opened');
    });

    test('connecting them mid-refusal makes it startable', () async {
      controller.primeFromConfig(
        const AppConfig(host: 'h', port: 8000, secure: false),
        glassesConnected: false,
      );
      expect(await controller.start(), isFalse);

      // The bridge reports them arriving.
      controller.primeFromConfig(
        const AppConfig(host: 'h', port: 8000, secure: false),
        glassesConnected: true,
      );
      expect(await controller.start(), isTrue);
    });

    test('a brief drop holds the session instead of ending it', () async {
      // Device-seen 2026-08-11: the glasses reported `disconnected` and were
      // back eleven seconds later on their own, but the session had already
      // ended for good. Walking between rooms should not end a conversation.
      await connect();
      expect(controller.state.isRunning, isTrue);

      glasses.emit('connectionState', {'state': 'disconnected'});
      await pump(5);

      expect(controller.state.isRunning, isTrue, reason: 'held, not ended');
      expect(controller.state.error, isNull);
      expect(controller.state.notice, contains('dropped'));
      // The microphone still has to go. With the glasses gone the sound comes
      // out of the loudspeaker and back into the mic listening to the room.
      expect(source.audioStarted, isFalse, reason: 'the mic must be released');
    });

    test('they come back inside the window and it carries on', () async {
      await connect();
      glasses.emit('connectionState', {'state': 'disconnected'});
      await pump(5);

      glasses.emit('connectionState', {'state': 'connected'});
      await pump(5);

      expect(controller.state.isRunning, isTrue);
      expect(controller.state.notice, isNull, reason: 'the banner must clear');
      expect(controller.state.error, isNull);
      expect(source.audioStarted, isTrue, reason: 'the mic has to come back');
    });

    test('they stay away and it ends, saying so', () async {
      await connect();
      glasses.emit('connectionState', {'state': 'disconnected'});
      await pump(5);
      // Past the grace window (10ms in this controller — see setUp).
      await Future<void>.delayed(const Duration(milliseconds: 40));
      await pump(5);

      expect(controller.state.isRunning, isFalse);
      expect(controller.state.error, contains('glasses'));
      expect(source.audioStarted, isFalse);
    });

    test('stopping cancels the wait, so no late error paints over it',
        () async {
      await connect();
      glasses.emit('connectionState', {'state': 'disconnected'});
      await pump(5);
      await controller.stop();

      await Future<void>.delayed(const Duration(milliseconds: 40));
      await pump(5);

      expect(controller.state.error, isNull,
          reason: 'a timer firing into a dead session is a lying screen');
    });
  });

  group('the feedback loop', () {
    /// Device-proven 2026-08-10: one English sentence came back as FOURTEEN
    /// translations, each the previous one looping in through the microphone
    /// at speaker volume. The platform AEC was not enough, and
    /// `echoTargetLanguage: false` did not catch a reverberated copy either.
    Iterable<Uint8List> audioFramesSent() =>
        fake.sentLog.whereType<Uint8List>().where(
              (b) => MediaFrame.decode(b).tag == FrameTag.inputAudio,
            );

    test('the mic is withheld while our own audio is playing', () async {
      await connect();
      player.playing = true; // the translation is coming out of the speaker
      source.audioCtl.add(Uint8List.fromList([1, 2, 3, 4]));
      source.audioCtl.add(Uint8List.fromList([5, 6, 7, 8]));
      await pump();
      expect(audioFramesSent(), isEmpty,
          reason: 'our own output was sent back up to be re-translated');
    });

    test('the mic returns the moment playback ends', () async {
      await connect();
      player.playing = true;
      source.audioCtl.add(Uint8List.fromList([1, 2, 3, 4]));
      await pump();
      expect(audioFramesSent(), isEmpty);

      player.playing = false;
      source.audioCtl.add(Uint8List.fromList([5, 6, 7, 8]));
      await pump();
      expect(audioFramesSent(), hasLength(1),
          reason: 'the room must be heard again as soon as we stop speaking');
    });

    test('captions-only never withholds — nothing is playing', () async {
      await connect();
      controller.setCaptionsOnly(true);
      player.playing = true; // stale flag; captions-only feeds no audio
      source.audioCtl.add(Uint8List.fromList([1, 2, 3, 4]));
      await pump();
      expect(audioFramesSent(), hasLength(1));
    });

    test('the mic is withheld while the PHONE is doing the talking', () async {
      // The guard used to ask only the PCM player. Moving the voice on-device
      // to cut the cost meant the translation never went through that player,
      // so the guard saw silence and held nothing — and the phone heard
      // itself: "and Uncle Javed" came back as "एंड अंकल जावेद", English in
      // Devanagari, translated again and paid for again (device-seen
      // 2026-08-14). A saving in one place silently disabled a defence in
      // another, and every test here passed throughout.
      await connect();
      player.playing = false; // nothing from the cloud — that is the point
      voice.speaking = true; // Android's own engine is mid-sentence
      source.audioCtl.add(Uint8List.fromList([1, 2, 3, 4]));
      source.audioCtl.add(Uint8List.fromList([5, 6, 7, 8]));
      await pump();

      expect(audioFramesSent(), isEmpty,
          reason: 'the phone sent its own voice up to be re-translated');
    });

    test('the mic returns when the phone stops talking', () async {
      await connect();
      player.playing = false;
      voice.speaking = true;
      source.audioCtl.add(Uint8List.fromList([1, 2, 3, 4]));
      await pump();
      expect(audioFramesSent(), isEmpty);

      voice.speaking = false;
      source.audioCtl.add(Uint8List.fromList([5, 6, 7, 8]));
      await pump();
      expect(audioFramesSent(), hasLength(1),
          reason: 'a translator that stays deaf after speaking is no use');
    });

    test('a mic that is held is still a LIVE mic', () async {
      // The watchdog must not mistake a long translation for a dead
      // microphone and end the session mid-sentence.
      await connect();
      player.playing = true;
      for (var i = 0; i < 5; i++) {
        source.audioCtl.add(Uint8List.fromList([1, 2, 3, 4]));
      }
      await pump();
      expect(controller.state.isRunning, isTrue);
      expect(controller.state.error, isNull);
    });
  });

  group('the audio route', () {
    /// The bug behind the loop: the session played the translation on the
    /// MEDIA path, where the platform echo canceller has no reference signal
    /// to subtract. `voice_audio_mode.dart` exists for exactly this and the
    /// assistant has used it since August; this screen forgot to switch it on.
    test('the voice path is claimed before anything plays', () async {
      final voice = _FakeVoiceAudioMode('applied');
      await controller.dispose();
      controller = build(voiceAudioMode: voice);
      await connect();
      expect(voice.enters, 1);
    });

    test('and given back when the session stops', () async {
      final voice = _FakeVoiceAudioMode('applied');
      await controller.dispose();
      controller = build(voiceAudioMode: voice);
      await connect();
      await controller.stop();
      expect(voice.exits, greaterThanOrEqualTo(1));
    });

    test('the canceller alone does NOT earn the mic back', () async {
      // Tried and disproved on device. With the voice path claimed and the
      // hold removed, the loop returned immediately — the "heard" pane filled
      // with our own Hindi, labelled English. At loudspeaker volume the
      // platform canceller does not get close enough, so claiming the voice
      // path is a help, not a substitute.
      final voice = _FakeVoiceAudioMode('applied');
      await controller.dispose();
      controller = build(voiceAudioMode: voice);
      await connect();

      player.playing = true;
      source.audioCtl.add(Uint8List.fromList([1, 2, 3, 4]));
      await pump();
      final sent = fake.sentLog.whereType<Uint8List>().where(
            (b) => MediaFrame.decode(b).tag == FrameTag.inputAudio,
          );
      expect(sent, isEmpty,
          reason: 'on the loudspeaker the mic must still be held');
    });

    test('the speaker route warns that speech will be missed', () async {
      // Fragments on continuous speech read as a broken feature unless the
      // user was told why.
      final voice = _FakeVoiceAudioMode('applied');
      await controller.dispose();
      controller = build(voiceAudioMode: voice);
      await connect();
      expect(controller.state.notice, contains('phone speaker'));
    });

    test('on a headset the mic is not withheld either', () async {
      // The platform declines to switch paths when sound already goes to a
      // headset or the glasses — which is also when there is no acoustic path
      // back into the microphone at all.
      final voice = _FakeVoiceAudioMode('skipped_external_route');
      await controller.dispose();
      controller = build(voiceAudioMode: voice);
      await connect();

      player.playing = true;
      source.audioCtl.add(Uint8List.fromList([1, 2, 3, 4]));
      await pump();
      expect(
        fake.sentLog.whereType<Uint8List>().where(
              (b) => MediaFrame.decode(b).tag == FrameTag.inputAudio,
            ),
        hasLength(1),
      );
      expect(controller.state.notice, isNull,
          reason: 'on a headset nothing is missed, so say nothing');
    });
  });
  group('a session that outlives its token', () {
    /// Access tokens live fifteen minutes; a translate session can run for an
    /// hour. This screen builds its own socket from a config snapshot taken
    /// when it opened, so the snapshot goes stale while the screen is still
    /// up — and every reconnect is then refused. On the device that showed as
    /// "Starting…" forever with 27 rejections in the server log and not one
    /// word transcribed (2026-08-14).
    test('a renewed token reaches the socket', () async {
      await connect();
      final renewed = const AppConfig(host: 'h', port: 8000, secure: false)
          .copyWith(authToken: 'fresh-token');

      controller.updateConfig(renewed);
      await pump(3);

      expect(fake.closed, isFalse,
          reason: 'a new token is not a reason to drop a live session');
    });

    test('and is used the next time it connects', () async {
      await connect();
      controller.updateConfig(
        const AppConfig(host: 'h', port: 8000, secure: false)
            .copyWith(authToken: 'fresh-token'),
      );
      await controller.stop();
      await pump(3);
      await controller.start();
      await pump(3);

      expect(lastUri?.queryParameters['token'], 'fresh-token',
          reason: 'the socket reconnected on the dead token');
    });
  });

  group('sentences translated out of order', () {
    /// Sentences are translated concurrently while the speaker keeps talking,
    /// so a translation almost always arrives AFTER the next sentence is
    /// already on screen. The client used to attach every translation to
    /// whatever was newest: on the device the first sentence of a paragraph
    /// lost its Hindi entirely and the second briefly wore it instead
    /// (2026-08-14).
    test('each translation lands under the words it came from', () async {
      await connect();

      fake.pushJson({'type': 'transcript', 'role': 'user', 'text': 'first.',
        'final': true, 'lang': 'ar', 'utterance': 0});
      fake.pushJson({'type': 'transcript', 'role': 'user', 'text': 'second.',
        'final': true, 'lang': 'ar', 'utterance': 1});
      // Now the translations come back — in the wrong order, as they do.
      fake.pushJson({'type': 'transcript', 'role': 'assistant',
        'text': 'दूसरा।', 'final': true, 'utterance': 1});
      fake.pushJson({'type': 'transcript', 'role': 'assistant',
        'text': 'पहला।', 'final': true, 'utterance': 0});
      await pump(5);

      final turns = controller.state.turns;
      expect(turns.length, 2);
      expect(turns[0].heard, 'first.');
      expect(turns[0].translated, 'पहला।',
          reason: 'the first sentence lost its translation');
      expect(turns[1].heard, 'second.');
      expect(turns[1].translated, 'दूसरा।');
    });

    test('a language named later updates the same card, not a new one', () {
      // The recogniser reports no language, so the server re-sends the heard
      // line once the translator has named it. That must not appear twice.
      return connect().then((_) async {
        fake.pushJson({'type': 'transcript', 'role': 'user', 'text': 'مرحبا.',
          'final': true, 'utterance': 0});
        await pump(3);
        fake.pushJson({'type': 'transcript', 'role': 'user', 'text': 'مرحبا.',
          'final': true, 'lang': 'ar', 'utterance': 0});
        await pump(3);

        expect(controller.state.turns.length, 1, reason: 'it was duplicated');
        expect(controller.state.turns.first.heardLang, 'ar');
      });
    });

    test('a translation for a sentence that scrolled away is dropped', () async {
      // Rather than landing on an innocent bystander.
      await connect();
      fake.pushJson({'type': 'transcript', 'role': 'user', 'text': 'here.',
        'final': true, 'utterance': 7});
      fake.pushJson({'type': 'transcript', 'role': 'assistant',
        'text': 'ghost', 'final': true, 'utterance': 999});
      await pump(5);

      expect(controller.state.turns.single.translated, isEmpty);
    });

    test('a server that sends no numbers still works', () async {
      // The old shape, and the mock, both omit it.
      await connect();
      fake.pushJson({'type': 'transcript', 'role': 'user', 'text': 'hola.',
        'final': true, 'lang': 'es'});
      fake.pushJson({'type': 'transcript', 'role': 'assistant',
        'text': 'नमस्ते।', 'final': true});
      await pump(5);

      expect(controller.state.turns.single.translated, 'नमस्ते।');
    });
  });
}
