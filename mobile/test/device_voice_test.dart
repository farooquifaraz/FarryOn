import 'dart:async';

import 'package:farryon/playback/device_voice.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_tts/flutter_tts.dart';

/// Saying the translation with the phone's own voice.
///
/// Why it exists: spoken audio is about 80% of what a minute of live
/// translation costs — $0.025 of $0.031 — and the cloud voice waits a second
/// and a half before its first sound. Android ships an engine on every phone
/// that is free and starts immediately.
///
/// What matters here is that a phone WITHOUT the voice data degrades honestly.
/// The translation is on screen either way; going quietly mute forever would
/// read as a broken feature, and the only fix (installing the language) is
/// something the user has to do.
class _FakeTts implements FlutterTts {
  _FakeTts({
    this.available = true,
    this.throwOnSpeak = false,
    this.hang = false,
  });

  final bool available;
  final bool throwOnSpeak;

  /// The engine that never answers: flutter_tts 4.2.5 on Android completes
  /// `speak` only from `onDone`; an interrupted or failed utterance leaves the
  /// future pending forever.
  final bool hang;
  final List<String> spoken = [];
  final List<String> languages = [];
  int stops = 0;
  VoidCallback? onCancel;
  ErrorHandler? onError;

  @override
  Future<dynamic> isLanguageAvailable(String lang) async => available;

  @override
  Future<dynamic> setLanguage(String lang) async => languages.add(lang);

  @override
  Future<dynamic> speak(String text, {bool? focus}) async {
    if (throwOnSpeak) throw Exception('engine is busy');
    spoken.add(text);
    if (hang) return Completer<dynamic>().future;
    return 1;
  }

  @override
  Future<dynamic> stop() async => stops++;

  @override
  void setCancelHandler(VoidCallback callback) => onCancel = callback;

  @override
  void setErrorHandler(ErrorHandler handler) => onError = handler;

  @override
  Future<dynamic> awaitSpeakCompletion(bool await_) async => 1;

  @override
  dynamic noSuchMethod(Invocation invocation) => Future<dynamic>.value(1);
}

void main() {
  test('it speaks the translation in the target language', () async {
    final tts = _FakeTts();
    final voice = DeviceVoice(tts: tts);

    expect(await voice.speak('बैठक चार बजे शुरू होगी।', 'hi'), isTrue);
    expect(tts.spoken, ['बैठक चार बजे शुरू होगी।']);
    expect(tts.languages, ['hi']);
  });

  test('a region variant still finds the language', () {
    // A phone with Hindi must not be told it lacks "hi-IN".
    final tts = _FakeTts();
    final voice = DeviceVoice(tts: tts);
    return voice.speak('नमस्ते', 'hi-IN').then((_) {
      expect(tts.languages, ['hi']);
    });
  });

  test('a phone without the voice says so once, not every sentence', () async {
    final tts = _FakeTts(available: false);
    final voice = DeviceVoice(tts: tts);

    expect(await voice.speak('नमस्ते', 'hi'), isFalse);
    expect(voice.cannotSpeak('hi'), isTrue);
    expect(await voice.speak('फिर से', 'hi'), isFalse);
    expect(tts.spoken, isEmpty, reason: 'it must not pretend to have spoken');
  });

  test('an engine that throws does not take the session with it', () async {
    // The translation is on screen. A failed voice is a smaller loss than a
    // crashed translator.
    final voice = DeviceVoice(tts: _FakeTts(throwOnSpeak: true));
    expect(await voice.speak('नमस्ते', 'hi'), isFalse);
  });

  test('empty text is not worth waking the engine for', () async {
    final tts = _FakeTts();
    expect(await DeviceVoice(tts: tts).speak('   ', 'hi'), isFalse);
    expect(tts.spoken, isEmpty);
  });

  test('stopping is safe, and safe to repeat', () async {
    final tts = _FakeTts();
    final voice = DeviceVoice(tts: tts);

    await voice.stop();
    await voice.speak('नमस्ते', 'hi');
    await voice.stop();

    expect(tts.stops, 2, reason: 'stop must always be allowed through');
  });

  test('an engine that never reports the end releases the microphone',
      () async {
    // One translation, then "listening" for the rest of the session: the
    // engine dropped the utterance without answering, the speaking count
    // never came down, and the echo guard held the mic shut (Vivo,
    // 2026-09-30).
    final tts = _FakeTts(hang: true);
    final voice = DeviceVoice(
      tts: tts,
      budgetFloor: const Duration(milliseconds: 40),
      budgetPerCharacter: Duration.zero,
    );

    final said = voice.speak('नमस्ते', 'hi');
    await Future<void>.delayed(const Duration(milliseconds: 5));
    expect(voice.isSpeakingWithin(Duration.zero), isTrue,
        reason: 'the phone is (as far as we know) talking');

    expect(await said.timeout(const Duration(seconds: 2)), isTrue);
    expect(voice.isSpeakingWithin(Duration.zero), isFalse,
        reason: 'past the budget the microphone is trusted again');
    expect(tts.stops, 1,
        reason: "the plugin's own speaking flag must not refuse the next one");
  });

  test('an interrupted utterance releases the microphone at once', () async {
    final tts = _FakeTts(hang: true);
    final voice = DeviceVoice(
      tts: tts,
      budgetFloor: const Duration(seconds: 30),
    );

    final said = voice.speak('a long sentence that a phone call cuts off', 'en');
    await Future<void>.delayed(const Duration(milliseconds: 5));
    tts.onCancel!();

    expect(await said.timeout(const Duration(seconds: 1)), isTrue);
    expect(voice.isSpeakingWithin(Duration.zero), isFalse);
    expect(tts.stops, 0, reason: 'nothing to stop: the engine already had');
  });

  test('two translations in quick succession are both said, in order',
      () async {
    // The plugin refuses (returns 0, says nothing) a sentence that arrives
    // while another is playing. Fast speech produces exactly that, and used
    // to lose every second sentence.
    final tts = _FakeTts();
    final voice = DeviceVoice(tts: tts);

    await Future.wait([
      voice.speak('पहला वाक्य', 'hi'),
      voice.speak('दूसरा वाक्य', 'hi'),
    ]);

    expect(tts.spoken, ['पहला वाक्य', 'दूसरा वाक्य']);
    expect(voice.isSpeakingWithin(Duration.zero), isFalse);
  });

  test('a sentence still waiting when the session stops stays unsaid',
      () async {
    final tts = _FakeTts(hang: true);
    final voice = DeviceVoice(
      tts: tts,
      budgetFloor: const Duration(seconds: 30),
    );

    final first = voice.speak('first', 'en');
    final second = voice.speak('second', 'en');
    await Future<void>.delayed(const Duration(milliseconds: 5));
    await voice.stop();

    expect(await first.timeout(const Duration(seconds: 1)), isTrue);
    expect(await second.timeout(const Duration(seconds: 1)), isTrue,
        reason: 'not a "no voice installed" failure — the session just ended');
    expect(tts.spoken, ['first']);
    expect(voice.isSpeakingWithin(Duration.zero), isFalse);
  });

  test('nothing is built until someone asks for sound', () async {
    // Constructing FlutterTts installs a platform method-call handler, which
    // needs a binding. Doing it in the translate controller's initialiser took
    // out thirty-two unrelated unit tests that never intended to speak. With no
    // engine injected, none is made until speak() is called — and stop() on a
    // silent session must not be what makes one.
    final voice = DeviceVoice();
    await voice.stop(); // would throw if it constructed FlutterTts here
    expect(voice.cannotSpeak('hi'), isFalse);
  });
}
