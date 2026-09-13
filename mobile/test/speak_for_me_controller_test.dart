import 'package:farryon/core/config.dart';
import 'package:farryon/features/accessibility/speak_for_me_controller.dart';
import 'package:farryon/playback/device_voice.dart';
import 'package:flutter_test/flutter_test.dart';

/// Stands in for Android's text-to-speech. `available` is the set of
/// languages this phone can say; anything else is refused the way the real
/// engine refuses — quietly, with a false.
class _FakeVoice implements DeviceVoice {
  final Set<String> available = {'en', 'ur', 'hi'};
  final List<String> said = <String>[];
  final List<String> saidIn = <String>[];
  int stops = 0;
  bool speaking = false;

  @override
  Future<bool> speak(String text, String language) async {
    final base = language.split('-').first;
    if (!available.contains(base)) return false;
    said.add(text);
    saidIn.add(language);
    return true;
  }

  @override
  Future<void> stop() async {
    stops++;
    speaking = false;
  }

  @override
  bool isSpeakingWithin(Duration tail) => speaking;

  @override
  bool cannotSpeak(String language) =>
      !available.contains(language.split('-').first);

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

void main() {
  late _FakeVoice voice;
  late SpeakForMeController controller;

  setUp(() {
    voice = _FakeVoice();
    controller = SpeakForMeController(language: 'ur', voice: voice);
  });

  tearDown(() => controller.dispose());

  group('saying a line', () {
    test('speaks it in the chosen language and keeps it, newest first',
        () async {
      expect(await controller.say('Thank you'), isTrue);
      expect(await controller.say('Please wait'), isTrue);

      expect(voice.said, ['Thank you', 'Please wait']);
      expect(voice.saidIn, everyElement('ur'));
      expect(controller.state.lines.map((l) => l.text),
          ['Please wait', 'Thank you']);
      expect(
          controller.state.lines,
          everyElement(
              isA<SpokenLine>().having((l) => l.spoken, 'spoken', isTrue)));
      expect(controller.state.speaking, isFalse);
      expect(controller.state.error, isNull);
    });

    test('trims, and says nothing for blank text', () async {
      expect(await controller.say('   '), isFalse);
      expect(await controller.say('  Yes  '), isTrue);
      expect(voice.said, ['Yes']);
      expect(controller.state.lines.single.text, 'Yes');
    });

    test('a new line cuts off the one still playing', () async {
      // The user pressed Speak again because the first line was wrong or the
      // moment moved on. Queueing would make the phone finish saying the
      // thing they retracted.
      final first = controller.say('Where is the bathroom?');
      // In flight: the controller reports speaking before the voice returns.
      expect(controller.state.speaking, isTrue);
      await first;
      expect(controller.state.speaking, isFalse);
      // Now put the voice mid-sentence and interrupt it.
      voice.speaking = true;
      final second = controller.say('No');
      expect(controller.state.speaking, isTrue);
      await second;
      expect(voice.said.last, 'No');
    });

    test('an in-flight line is replaced, not queued', () async {
      // Two calls before either resolves: the second must stop the first.
      final a = controller.say('One');
      final b = controller.say('Two');
      await Future.wait([a, b]);
      expect(voice.stops, greaterThanOrEqualTo(1),
          reason: 'the second line has to stop the first');
      expect(voice.said, ['One', 'Two']);
    });
  });

  group('a language the phone cannot say', () {
    test('keeps the line on screen, marks it unspoken, and says why', () async {
      controller.setLanguage('bn');
      expect(await controller.say('Help me'), isFalse);

      expect(voice.said, isEmpty);
      final line = controller.state.lines.single;
      expect(line.text, 'Help me', reason: 'the text can still be shown');
      expect(line.spoken, isFalse);
      expect(controller.state.error, contains('voice'));
      expect(controller.state.error, contains('Add one'),
          reason: 'the fix is named, not just the failure');
    });

    test('the message clears once a line is spoken or the language changes',
        () async {
      controller.setLanguage('bn');
      await controller.say('Help me');
      expect(controller.state.error, isNotNull);

      controller.setLanguage('en');
      expect(controller.state.error, isNull,
          reason: '"no Bengali voice" says nothing about English');

      controller.setLanguage('bn');
      await controller.say('Help me');
      expect(controller.state.error, isNotNull);
      controller.setLanguage('en');
      await controller.say('Help me');
      expect(controller.state.error, isNull);
    });
  });

  group('the list', () {
    test('is capped so an afternoon of talking does not grow forever',
        () async {
      for (var i = 0; i < SpeakForMeController.maxLines + 5; i++) {
        await controller.say('line $i');
      }
      expect(controller.state.lines.length, SpeakForMeController.maxLines);
      expect(controller.state.lines.first.text,
          'line ${SpeakForMeController.maxLines + 4}',
          reason: 'newest kept, oldest dropped');
    });

    test('latest is the newest line', () async {
      expect(controller.state.latest, isNull);
      await controller.say('A');
      await controller.say('B');
      expect(controller.state.latest!.text, 'B');
    });
  });

  group('stop', () {
    test('silences the voice and reports idle', () async {
      voice.speaking = true;
      await controller.stop();
      expect(voice.stops, 1);
      expect(controller.state.speaking, isFalse);
    });
  });

  group('the starting language', () {
    const base = AppConfig(host: 'h', port: 1, secure: false);

    test('is the saved choice when there is one', () {
      expect(
        defaultSpeakLanguage(
            base.copyWith(speakLanguage: 'ar', primaryLanguage: 'Hindi')),
        'ar',
      );
    });

    test('otherwise follows the primary language from Settings', () {
      expect(
          defaultSpeakLanguage(base.copyWith(primaryLanguage: 'Urdu')), 'ur');
      expect(
          defaultSpeakLanguage(base.copyWith(primaryLanguage: 'hindi')), 'hi',
          reason: 'case must not matter');
    });

    test('falls back to English only when nothing matches', () {
      expect(
        defaultSpeakLanguage(base.copyWith(primaryLanguage: 'Klingon')),
        'en',
      );
    });
  });

  test('starter phrases are short enough to be said in a hurry', () {
    expect(kStarterPhrases, isNotEmpty);
    for (final p in kStarterPhrases) {
      expect(p.trim(), p, reason: 'no stray whitespace: "$p"');
      expect(p.length, lessThan(60), reason: '"$p" is too long for a chip');
    }
    expect(kStarterPhrases.toSet().length, kStarterPhrases.length,
        reason: 'no duplicates');
  });
}
