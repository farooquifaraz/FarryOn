import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'core/theme.dart';
import 'features/auth/splash_screen.dart';
import 'features/live/live_screen.dart';
import 'state/auth.dart';

/// Root widget: "Midnight Aurora" theming and the auth-gated home route.
class FarryOnApp extends StatelessWidget {
  const FarryOnApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Farry',
      debugShowCheckedModeBanner: false,
      theme: Aurora.theme(),
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
class AuthGate extends ConsumerWidget {
  const AuthGate({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authProvider);
    if (auth.isRestoring) return const _RestoreSplash();
    return auth.isSignedIn ? const LiveScreen() : const SplashScreen();
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
