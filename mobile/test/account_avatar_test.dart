import 'package:farryon/features/live/live_screen.dart';
import 'package:flutter_test/flutter_test.dart';

/// The avatar's only job is to answer "am I signed in, and as whom?" at a
/// glance, so the initial must always be *something* recognisable — a blank
/// circle would look like a rendering bug, and "?" would look like an error
/// rather than a person.
void main() {
  group('account initial', () {
    test('prefers the display name', () {
      expect(accountInitialFor('Faraz Farooqui', 'x@example.com'), 'F');
    });

    test('falls back to the email when there is no name', () {
      // Google gives a name; a password sign-up may not.
      expect(accountInitialFor(null, 'zain@example.com'), 'Z');
      expect(accountInitialFor('   ', 'zain@example.com'), 'Z');
    });

    test('never renders blank when both are empty', () {
      expect(accountInitialFor(null, ''), '•');
    });

    test('upper-cases a lowercase source', () {
      expect(accountInitialFor('faraz', ''), 'F');
    });

    test('takes a whole grapheme, not half of one', () {
      // A naive `source[0]` would slice a surrogate pair or a combining mark in
      // half and render a replacement glyph — the initial has to survive a
      // non-Latin or emoji name.
      //
      // 'फ़' is one grapheme built from two code points (फ + nukta), and the
      // nukta is not decoration: फ is "pha", फ़ is "fa". Dropping it would show
      // the wrong letter, not just a plainer one.
      expect(accountInitialFor('फ़राज़', ''), 'फ़');
      expect(accountInitialFor('👨‍💻 dev', ''), '👨‍💻');
    });
  });
}
