import 'dart:async';

import 'package:farryon/app.dart';
import 'package:farryon/state/auth.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// Signing in must dismiss the auth screens.
///
/// This is the regression test for a shipped bug: Sign In and Create Account are
/// *pushed on top of* the `home` that [AuthGate] swaps, and a route pushed above
/// home survives home changing underneath it. So a successful Google sign-in
/// left the login form covering a live, connected LiveScreen — Farry running and
/// talking to the backend behind a screen that still asked you to sign in.
///
/// [AuthGate] itself is deliberately not exercised here: its signed-in branch is
/// LiveScreen, which opens a WebSocket and a camera on mount. What broke was the
/// navigator's route stack, so that is what this pins.
class _FakeAuth extends AuthNotifier {
  @override
  AuthState build() => const AuthState.signedOut();

  void completeSignIn() =>
      state = const AuthState.signedIn(email: 'faraz@example.com');

  @override
  Future<void> signOut() async => state = const AuthState.signedOut();
}

void main() {
  late _FakeAuth auth;

  /// The real [FarryOnApp] listener + navigator, over a home we can mount in a
  /// test. Mirrors the shape of the app: a home route, with auth screens pushed
  /// on top of it.
  Widget host(GlobalKey<NavigatorState> navKey) {
    return ProviderScope(
      overrides: [authProvider.overrideWith(() => auth)],
      child: Consumer(
        builder: (context, ref, _) {
          ref.listen<AuthState>(authProvider, (previous, next) {
            if (next.isSignedIn && previous?.isSignedIn != true) {
              navKey.currentState?.popUntil((route) => route.isFirst);
            }
          });
          return MaterialApp(
            navigatorKey: navKey,
            home: const Scaffold(body: Text('home')),
          );
        },
      ),
    );
  }

  setUp(() => auth = _FakeAuth());

  testWidgets('a successful sign-in pops the auth screens off home',
      (tester) async {
    final navKey = GlobalKey<NavigatorState>();
    await tester.pumpWidget(host(navKey));

    unawaited(navKey.currentState!.push(MaterialPageRoute<void>(
      builder: (_) => const Scaffold(body: Text('login screen')),
    )));
    await tester.pumpAndSettle();
    expect(find.text('login screen'), findsOneWidget);

    auth.completeSignIn();
    await tester.pumpAndSettle();

    expect(find.text('login screen'), findsNothing,
        reason: 'the login screen must not survive a successful sign-in');
    expect(find.text('home'), findsOneWidget);
  });

  testWidgets('signup stacked on login is popped too', (tester) async {
    // Create Account pushes on top of Sign In, so signing in with Google from
    // the signup screen leaves *two* routes above home. popUntil(isFirst)
    // clears both; a single pop would have left the login screen showing.
    final navKey = GlobalKey<NavigatorState>();
    await tester.pumpWidget(host(navKey));

    for (final name in ['login screen', 'signup screen']) {
      unawaited(navKey.currentState!.push(MaterialPageRoute<void>(
        builder: (_) => Scaffold(body: Text(name)),
      )));
    }
    await tester.pumpAndSettle();
    expect(find.text('signup screen'), findsOneWidget);

    auth.completeSignIn();
    await tester.pumpAndSettle();

    expect(find.text('signup screen'), findsNothing);
    expect(find.text('login screen'), findsNothing);
    expect(find.text('home'), findsOneWidget);
  });

  testWidgets('signing out does not pop anything', (tester) async {
    // Sign-out swaps home back to the splash on its own. Popping here would
    // fight whatever the user opened next.
    final navKey = GlobalKey<NavigatorState>();
    await tester.pumpWidget(host(navKey));

    auth.completeSignIn();
    await tester.pumpAndSettle();

    unawaited(navKey.currentState!.push(MaterialPageRoute<void>(
      builder: (_) => const Scaffold(body: Text('settings')),
    )));
    await tester.pumpAndSettle();

    unawaited(auth.signOut());
    await tester.pumpAndSettle();

    expect(find.text('settings'), findsOneWidget);
  });

  testWidgets('a rebuild while already signed in pops nothing', (tester) async {
    // The listener keys off the *transition* to signedIn, not the state: a
    // token rotation re-emitting signedIn must not yank away a screen the user
    // is reading.
    final navKey = GlobalKey<NavigatorState>();
    await tester.pumpWidget(host(navKey));

    auth.completeSignIn();
    await tester.pumpAndSettle();

    unawaited(navKey.currentState!.push(MaterialPageRoute<void>(
      builder: (_) => const Scaffold(body: Text('your stuff')),
    )));
    await tester.pumpAndSettle();

    auth.completeSignIn(); // e.g. refresh rotated the tokens
    await tester.pumpAndSettle();

    expect(find.text('your stuff'), findsOneWidget,
        reason: 'a token rotation must not close the screen you are on');
  });
}
