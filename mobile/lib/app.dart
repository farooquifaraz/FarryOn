import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'core/config_store.dart';
import 'core/theme.dart';
import 'features/auth/splash_screen.dart';
import 'features/live/live_screen.dart';
import 'features/onboarding/permission_intro_screen.dart';
import 'state/auth.dart';

/// Root widget: "Midnight Aurora" theming and the auth-gated home route.
class FarryOnApp extends ConsumerStatefulWidget {
  const FarryOnApp({super.key});

  @override
  ConsumerState<FarryOnApp> createState() => _FarryOnAppState();
}

class _FarryOnAppState extends ConsumerState<FarryOnApp> {
  final _navigatorKey = GlobalKey<NavigatorState>();

  @override
  Widget build(BuildContext context) {
    // Signing in has to dismiss the auth screens, and only this listener can do
    // it: [AuthGate] swaps what `home` builds, but Sign In and Create Account
    // are *pushed on top of* home, and a route pushed above home survives home
    // changing underneath it. Without this, a successful sign-in left the login
    // screen sitting over a live, connected LiveScreen — Farry running, camera
    // on, talking to the backend, while the user still stared at a login form
    // and reasonably concluded it had failed.
    //
    // It lives here rather than in each screen because there are five ways in
    // (password, 2FA, Google — from either the login or the signup screen), and
    // a per-screen pop is a thing you can forget on the sixth. This cannot be
    // forgotten: every path ends at signedIn.
    ref.listen<AuthState>(authProvider, (previous, next) {
      if (next.isSignedIn && previous?.isSignedIn != true) {
        _navigatorKey.currentState?.popUntil((route) => route.isFirst);
      }
    });

    return MaterialApp(
      title: 'Farry',
      debugShowCheckedModeBanner: false,
      theme: Aurora.theme(),
      navigatorKey: _navigatorKey,
      home: const AuthGate(),
    );
  }
}

/// Shows [SplashScreen] (and the sign-in/sign-up it leads to) until a
/// FarryOn session exists, then [LiveScreen].
///
/// Must gate at the `home:` level: [LiveScreen] auto-connects its WebSocket
/// in a post-frame callback, so it may only mount once the signed-in config
/// (including the `?token=` for the handshake) is final — which
/// [AuthNotifier] guarantees before it ever reports signedIn. The brief
/// [_RestoreSplash] on cold start is what buys that guarantee.
class AuthGate extends ConsumerStatefulWidget {
  const AuthGate({super.key});

  @override
  ConsumerState<AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends ConsumerState<AuthGate> {
  /// Whether the first-run permission introduction still has to be shown.
  ///
  /// Read once into state rather than watched: it is written from inside the
  /// screen below, and a provider rebuilding the gate mid-flow would swap the
  /// screen out from under the platform dialog it is waiting on.
  late bool _introPending = !ConfigStore.permissionIntroSeen();

  @override
  Widget build(BuildContext context) {
    final auth = ref.watch(authProvider);
    if (auth.isRestoring) return const _RestoreSplash();
    if (!auth.isSignedIn) return const SplashScreen();
    // Sits between signing in and the live screen ON PURPOSE. LiveScreen
    // connects and asks for the microphone the moment it mounts, so anything
    // explaining the permissions has to come first or it explains a dialog
    // the user has already answered.
    if (_introPending) {
      return PermissionIntroScreen(
        onDone: () => setState(() => _introPending = false),
      );
    }
    return const LiveScreen();
  }
}

/// Cold-start holding screen while the stored session is rotated. Wears the
/// auth backdrop so a returning user sees the brand for the moment it takes,
/// rather than a flash of a different colour before the app appears.
class _RestoreSplash extends StatelessWidget {
  const _RestoreSplash();

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      backgroundColor: Color(0xFF06140F),
      body: DecoratedBox(
        decoration: BoxDecoration(gradient: Aurora.authBackdrop),
        child: Center(
          child: SizedBox(
            width: 26,
            height: 26,
            child: CircularProgressIndicator(strokeWidth: 2, color: Aurora.neon),
          ),
        ),
      ),
    );
  }
}
