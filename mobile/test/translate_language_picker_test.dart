import 'package:farryon/features/translate/translate_language_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// Telling someone BEFORE a conversation that their phone cannot speak the
/// language they picked.
///
/// Translation is voiced by the phone's own engine now — that is what took a
/// minute of translation from $0.031 to $0.0055. The cost is that a phone only
/// has the voices someone installed, so a target the engine cannot say leaves
/// the translation on screen and silent. Finding that out halfway through a
/// doctor's appointment is the failure this prevents.
void main() {
  Future<void> show(
    WidgetTester tester, {
    InstalledVoices? voices,
    Future<bool> Function()? onAdd,
  }) async {
    await tester.pumpWidget(MaterialApp(
      home: TranslateLanguagePicker(
        selected: 'hi',
        installedVoices: voices,
        onAddVoices: onAdd,
      ),
    ));
    await tester.pumpAndSettle();
  }

  testWidgets('a language the phone cannot say is marked', (tester) async {
    await show(tester, voices: () async => {'en', 'hi'});
    // Arabic is not in the set, so it carries the muted mark.
    expect(find.byIcon(Icons.volume_off_outlined), findsWidgets);
  });

  testWidgets('nothing is marked when the engine cannot be asked',
      (tester) async {
    // An unknown answer must not paint every language as silent — that would
    // be a screen full of warnings about a problem nobody has.
    await show(tester, voices: () async => <String>{});
    expect(find.byIcon(Icons.volume_off_outlined), findsNothing);
  });

  testWidgets('and nothing is marked when we never ask', (tester) async {
    await show(tester);
    expect(find.byIcon(Icons.volume_off_outlined), findsNothing);
  });

  testWidgets('the offer appears when a voice is missing', (tester) async {
    await show(tester, voices: () async => {'en'}, onAdd: () async => true);
    expect(find.text('Add voices'), findsOneWidget);
  });

  testWidgets('a phone we could not ask is not nagged', (tester) async {
    // Separate test on purpose: pumping the same widget twice updates the
    // existing State instead of re-running initState, so the second case
    // silently kept the first one's answer.
    await show(tester, voices: () async => <String>{}, onAdd: () async => true);
    expect(find.text('Add voices'), findsNothing);
  });

  testWidgets('tapping it opens the installer', (tester) async {
    var opened = false;
    await show(
      tester,
      voices: () async => {'en'},
      onAdd: () async {
        opened = true;
        return true;
      },
    );
    await tester.tap(find.text('Add voices'));
    await tester.pumpAndSettle();
    expect(opened, isTrue);
  });

  testWidgets('a phone with no such screen is told where to go', (tester) async {
    // Rather than a button that quietly does nothing.
    await show(tester, voices: () async => {'en'}, onAdd: () async => false);
    await tester.tap(find.text('Add voices'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Text-to-speech'), findsOneWidget);
  });

  testWidgets('picking a language still returns it', (tester) async {
    // The marks are information, not a lock: someone may want the text alone.
    String? picked;
    await tester.pumpWidget(MaterialApp(
      home: Builder(
        builder: (context) => ElevatedButton(
          onPressed: () async {
            picked = await TranslateLanguagePicker.open(
              context,
              'hi',
              installedVoices: () async => {'en'},
            );
          },
          child: const Text('open'),
        ),
      ),
    ));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('العربية'));
    await tester.pumpAndSettle();
    expect(picked, 'ar');
  });
}
