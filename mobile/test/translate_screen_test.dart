import 'package:farryon/features/translate/translate_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// Regression cover for the one failure this screen can cause that the user
/// cannot recover from on their own.
///
/// The screen disconnects the assistant on entry and promises, in a banner,
/// that she "comes back on her own when you leave this screen". Reconnecting
/// happens in `dispose()`. A `late final x = ref.read(...)` field resolves on
/// FIRST ACCESS — which, for a field only used in dispose, is *after* the
/// element is disposed. That threw `Bad state: Cannot use "ref" after the
/// widget was disposed`, the reconnect never ran, and the phone sat on
/// "Session ended" (device-seen 2026-08-09).
///
/// Every assertion here is about teardown, because that is where it broke.
void main() {
  testWidgets('leaving the screen tears down without touching a dead ref',
      (tester) async {
    await tester.pumpWidget(
      const ProviderScope(
        child: MaterialApp(home: TranslateScreen()),
      ),
    );
    await tester.pump();
    expect(find.text('Live translation'), findsOneWidget);
    // With no glasses connected — which is what a test binding has — the
    // screen explains why it cannot run rather than offering a button that
    // would fail.
    expect(
      find.textContaining('Connect your glasses'),
      findsOneWidget,
    );

    // Replace the route, disposing the screen, and let the detached
    // `_restore()` future run.
    await tester.pumpWidget(
      const ProviderScope(
        child: MaterialApp(home: SizedBox.shrink()),
      ),
    );
    await tester.pump(const Duration(milliseconds: 50));

    expect(
      tester.takeException(),
      isNull,
      reason: 'dispose must not throw — a thrown exception here silently '
          'leaves the assistant disconnected',
    );
  });

  testWidgets('tearing down without ever tapping anything is also safe',
      (tester) async {
    // The original bug hid behind the fact that tapping the mic happened to
    // initialise one of the two lazy fields first. A user who opens the screen
    // and immediately backs out took a different path through the same code.
    await tester.pumpWidget(
      const ProviderScope(
        child: MaterialApp(home: TranslateScreen()),
      ),
    );
    await tester.pumpWidget(
      const ProviderScope(
        child: MaterialApp(home: SizedBox.shrink()),
      ),
    );
    await tester.pump(const Duration(milliseconds: 50));
    expect(tester.takeException(), isNull);
  });
}
