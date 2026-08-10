import 'package:farryon/features/translate/translate_languages.dart';
import 'package:flutter_test/flutter_test.dart';

/// The language list is the one place where being wrong is invisible: a code
/// the model does not know is silently replaced with English, which looks
/// exactly like the picker doing nothing.
void main() {
  group('the list', () {
    test('covers what the model supports, with no duplicates', () {
      final codes = kTranslateLanguages.map((l) => l.code).toList();
      expect(codes.length, 78);
      expect(codes.toSet().length, codes.length, reason: 'duplicate code');
    });

    test('leads with the languages this product is actually used in', () {
      // A picker that opens on Afrikaans makes everyone scroll.
      expect(kTranslateLanguages.take(4).map((l) => l.code),
          containsAll(['hi', 'en', 'ur', 'ar']));
    });

    test('every entry has a native name', () {
      for (final l in kTranslateLanguages) {
        expect(l.native.trim(), isNotEmpty, reason: l.code);
        expect(l.name.trim(), isNotEmpty, reason: l.code);
      }
    });

    test('offers no region variants the model cannot honour', () {
      // Vendor apps list "Arabic (Qatar)" and friends. This model has one
      // `ar`; offering the choice would hand back plain Arabic with nothing
      // on screen to say why.
      const real = {'pt-BR', 'pt-PT', 'zh-Hans', 'zh-Hant'};
      for (final l in kTranslateLanguages) {
        if (l.code.contains('-')) {
          expect(real, contains(l.code), reason: '${l.code} is invented');
        }
      }
    });
  });

  group('search', () {
    TranslateLanguage find(String code) =>
        kTranslateLanguages.firstWhere((l) => l.code == code);

    test('finds a language by its English name', () {
      expect(find('ta').matches('tam'), isTrue);
    });

    test('finds it by its own script', () {
      // Matters when the phone keyboard is already set to that language.
      expect(find('hi').matches('हिन्दी'), isTrue);
      expect(find('ar').matches('العربية'), isTrue);
    });

    test('a short query matches only the start of a word', () {
      expect(find('hi').matches('hi'), isTrue);
      // Swahili's native name is Kiswahili. Two letters is exactly what
      // someone types when they know their language is near the top, so it
      // must not drag in every name with those letters buried inside.
      expect(find('sw').matches('hi'), isFalse);
    });

    test('a longer query may match anywhere', () {
      expect(find('sw').matches('wahili'), isTrue);
    });

    test('a word inside a bracketed name is still findable', () {
      expect(find('pt-BR').matches('brazil'), isTrue);
    });

    test('an empty query matches everything', () {
      expect(kTranslateLanguages.every((l) => l.matches('  ')), isTrue);
    });
  });

  group('writing direction', () {
    test('the right-to-left scripts are marked as such', () {
      // Left-aligning these is what made a correct Urdu translation look
      // broken: right words, wrong side, full stop at the wrong end.
      for (final code in ['ar', 'ur', 'fa', 'he', 'sd']) {
        expect(isRtlLanguage(code), isTrue, reason: code);
      }
    });

    test('everything else is left-to-right', () {
      for (final code in ['hi', 'en', 'zh-Hans', 'ta', 'ru', 'ja']) {
        expect(isRtlLanguage(code), isFalse, reason: code);
      }
    });

    test('an unknown or absent language is not assumed to be RTL', () {
      expect(isRtlLanguage(null), isFalse);
      expect(isRtlLanguage('xx'), isFalse);
    });
  });
}
