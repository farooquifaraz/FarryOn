/// Signing up must not pop the screen the app lives on.
///
/// `FarryApp` listens for `signedIn` and runs `popUntil(isFirst)` itself —
/// added because five different flows reach a signed-in state and each one
/// having its own pop is a thing you forget on the sixth. The sign-up screen
/// kept its pop from before that listener existed, so both ran: the listener
/// unwound the stack while `signUp()` was still awaiting, and the screen's own
/// pop then removed the first route as well.
///
/// An empty Navigator draws nothing. On the phone it reads as a dead app —
/// black, with the system bars dimmed — while underneath it is signed in,
/// connected and working. A fresh launch "fixes" it, which is what made it
/// look like a rendering fault for two days (S23 and vivo V2246,
/// 2026-08-15/16).
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// The two pops, in the order they really happen.
///
/// This models the navigator rather than driving the sign-up screen, which
/// would need a backend, a keystore and a Google plugin to reach the same
/// three lines. What is being pinned down is the arithmetic: one unwind is
/// correct, a second one empties the stack.
void main() {
  testWidgets('a second pop after popUntil empties the navigator',
      (tester) async {
    final key = GlobalKey<NavigatorState>();
    await tester.pumpWidget(
      MaterialApp(
        navigatorKey: key,
        home: const Scaffold(body: Text('home')),
      ),
    );

    // Splash -> sign up, the real depth when someone makes an account.
    unawaited(key.currentState!.push(
      MaterialPageRoute<void>(builder: (_) => const Scaffold(body: Text('signup'))),
    ));
    await tester.pumpAndSettle();
    expect(find.text('signup'), findsOneWidget);

    // What FarryApp does the moment tokens land.
    key.currentState!.popUntil((r) => r.isFirst);
    await tester.pumpAndSettle();
    expect(find.text('home'), findsOneWidget,
        reason: 'the listener alone lands on the app');

    // What the sign-up screen used to do afterwards.
    key.currentState!.pop();
    await tester.pumpAndSettle();

    expect(
      find.text('home'),
      findsNothing,
      reason: 'the extra pop took the app screen with it — this is the black '
          'screen, and it is why sign-up showed it and sign-in never did',
    );
  });
}
