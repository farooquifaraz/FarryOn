import 'package:farryon/playback/device_voice.dart';
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
  _FakeTts({this.available = true, this.throwOnSpeak = false});

  final bool available;
  final bool throwOnSpeak;
  final List<String> spoken = [];
  final List<String> languages = [];
  int stops = 0;

  @override
  Future<dynamic> isLanguageAvailable(String lang) async => available;

  @override
  Future<dynamic> setLanguage(String lang) async => languages.add(lang);

  @override
  Future<dynamic> speak(String text, {bool? focus}) async {
    if (throwOnSpeak) throw Exception('engine is busy');
    spoken.add(text);
    return 1;
  }

  @override
  Future<dynamic> stop() async => stops++;

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
