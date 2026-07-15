import 'package:flutter/material.dart';

import 'login_screen.dart';
import 'signup_screen.dart';
import 'widgets/auth_bits.dart';
import 'widgets/auth_scaffold.dart';

/// The front door: brand mark, then the only two choices a signed-out person
/// has. Deliberately holds nothing else — no fields, no server row — so the
/// first thing the app says is what it is, not what it needs.
///
/// Both routes push on top of this, so system-back always lands here rather
/// than out of the app.
class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return AuthScaffold(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 26),
        child: Column(
          children: [
            const Expanded(
              child: Center(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    AuthLogo(size: 130),
                    SizedBox(height: 18),
                    Text(
                      'FarryOn',
                      style: TextStyle(
                        fontSize: 27,
                        fontWeight: FontWeight.w700,
                        letterSpacing: -0.4,
                        color: Colors.white,
                      ),
                    ),
                  ],
                ),
              ),
            ),
            Padding(
              padding: const EdgeInsets.only(bottom: 56),
              child: Column(
                children: [
                  AuthCtaButton(
                    label: 'Sign In',
                    onPressed: () => Navigator.of(context).push(
                      MaterialPageRoute<void>(
                        builder: (_) => const LoginScreen(),
                      ),
                    ),
                  ),
                  const SizedBox(height: 12),
                  AuthWhiteButton(
                    label: 'Create Account',
                    onPressed: () => SignupScreen.open(context),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
