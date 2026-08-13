import 'package:farryon/features/translate/translate_state.dart';
import 'package:farryon/features/translate/translate_transcript.dart';
import 'package:flutter_test/flutter_test.dart';

/// What gets written down when someone keeps a translation.
///
/// The screen is a live view — provisional lines, colours, a capped backlog.
/// A saved copy is read later by someone who was not in the room, so it has to
/// stand on its own.
void main() {
  TranslateTurn _turn(
    String heard, {
    String? lang,
    String translated = '',
    bool finalised = true,
    bool same = false,
  }) =>
      TranslateTurn(
        heard: heard,
        heardLang: lang,
        translated: translated,
        heardFinal: finalised,
        sameLanguage: same,
      );

  final at = DateTime(2026, 8, 11, 18, 14);

  test('both sides of every line are kept, with the language named', () {
    final note = renderTranslationNote(
      turns: [
        _turn('La reunión empieza a las 4:30.',
            lang: 'es', translated: 'बैठक 4:30 बजे शुरू होगी।'),
      ],
      targetLanguage: 'hi',
      at: at,
    );

    expect(note, contains('Live translation → हिन्दी'));
    expect(note, contains('11 Aug 2026, 6:14 pm'));
    expect(note, contains('[Español] La reunión empieza a las 4:30.'));
    expect(note, contains('→ बैठक 4:30 बजे शुरू होगी।'));
  });

  test('a half-heard line is left out', () {
    // Provisional text is the model still thinking. In a record it is noise,
    // and worse, it can be a sentence that was never actually said.
    final note = renderTranslationNote(
      turns: [
        _turn('Bonjour.', lang: 'fr', translated: 'नमस्ते।'),
        _turn('la réu', lang: 'fr', finalised: false),
      ],
      targetLanguage: 'hi',
      at: at,
    );

    expect(note, contains('Bonjour.'));
    expect(note, isNot(contains('la réu')));
  });

  test('a deliberate silence says so, rather than looking like a failure', () {
    final note = renderTranslationNote(
      turns: [_turn('बैठक 4:30 बजे शुरू होगी।', lang: 'hi', same: true)],
      targetLanguage: 'hi',
      at: at,
    );

    expect(note, contains('already in हिन्दी'));
  });

  test('a language the model named but the list only has variants of', () {
    // Detection answers with a bare `zh`; the target list carries zh-Hans and
    // zh-Hant. This rendered as the literal string "zh" on screen once.
    final note = renderTranslationNote(
      turns: [_turn('会议将于下午4点半开始', lang: 'zh', translated: 'बैठक…')],
      targetLanguage: 'hi',
      at: at,
    );

    expect(note, contains('[中文]'));
  });

  group('is there anything to save', () {
    test('no, while nothing has settled', () {
      expect(hasSomethingToSave(const []), isFalse);
      expect(
        hasSomethingToSave([_turn('la réu', finalised: false)]),
        isFalse,
        reason: 'the button must not offer to save a blank note',
      );
      expect(hasSomethingToSave([_turn('   ')]), isFalse);
    });

    test('yes, once one line has', () {
      expect(hasSomethingToSave([_turn('Bonjour.', lang: 'fr')]), isTrue);
    });
  });
}
