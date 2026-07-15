@Tags(['preview'])
library;

import 'package:farryon/core/theme.dart';
import 'package:farryon/core/ui.dart';
import 'package:farryon/features/auth/widgets/auth_bits.dart';
import 'package:farryon/features/auth/widgets/auth_scaffold.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// Renders the sign-in layout to a PNG so it can actually be LOOKED at:
/// `flutter test --update-goldens test/auth_preview_test.dart`, then open
/// test/goldens/login_screen.png.
///
/// Not a regression gate — it's excluded from the default run (see the
/// `preview` tag in dart_test.yaml) because golden bytes differ per platform
/// and would fail in CI for no useful reason. It exists to review the design.
void main() {
  testWidgets('login screen preview', (tester) async {
    tester.view.physicalSize = const Size(1080, 2200);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(
      MaterialApp(
        theme: Aurora.theme(),
        home: MediaQuery(
          // Freeze the drift so the golden is deterministic.
          data: const MediaQueryData(disableAnimations: true),
          child: AuthScaffold(
            child: ListView(
              padding: const EdgeInsets.fromLTRB(22, 32, 22, 32),
              children: [
                const AuthBrand(),
                const SizedBox(height: 34),
                const Text(
                  'Welcome back',
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    color: Aurora.textPrimary,
                    fontSize: 27,
                    fontWeight: FontWeight.w700,
                    letterSpacing: -0.5,
                  ),
                ),
                const SizedBox(height: 7),
                const Text(
                  'Sign in to sync your notes, reminders, and glasses.',
                  textAlign: TextAlign.center,
                  style: TextStyle(
                      color: Aurora.textMuted, fontSize: 13.5, height: 1.45),
                ),
                const SizedBox(height: 28),
                const TextField(
                  decoration: InputDecoration(
                    labelText: 'Email address',
                    hintText: 'you@example.com',
                    prefixIcon: Icon(Icons.mail_outline_rounded, size: 20),
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  obscureText: true,
                  decoration: InputDecoration(
                    labelText: 'Password',
                    prefixIcon: const Icon(Icons.lock_outline_rounded, size: 20),
                    border: const OutlineInputBorder(),
                    suffixIcon: IconButton(
                      icon: const Icon(Icons.visibility_rounded,
                          color: Aurora.textMuted, size: 20),
                      onPressed: () {},
                    ),
                  ),
                ),
                const SizedBox(height: 22),
                GradientButton(
                  label: 'Sign in',
                  icon: Icons.arrow_forward_rounded,
                  onPressed: () {},
                ),
                const SizedBox(height: 16),
                const AuthDivider('or'),
                const SizedBox(height: 16),
                GoogleButton(onPressed: () {}),
                const SizedBox(height: 18),
                const Wrap(
                  alignment: WrapAlignment.center,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    Text('New to Farry? ',
                        style:
                            TextStyle(color: Aurora.textMuted, fontSize: 13)),
                    Text('Create an account',
                        style: TextStyle(
                            color: Aurora.mint,
                            fontSize: 13,
                            fontWeight: FontWeight.w700)),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await expectLater(
      find.byType(AuthScaffold),
      matchesGoldenFile('goldens/login_screen.png'),
    );
  });

  /// The Google mark is drawn, not shipped as an asset — so it has to be
  /// eyeballed at size to confirm it reads as *their* G and not a smudge.
  testWidgets('google button closeup', (tester) async {
    tester.view.physicalSize = const Size(1080, 900);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(
      MaterialApp(
        theme: Aurora.theme(),
        home: Scaffold(
          backgroundColor: Aurora.base,
          body: Center(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  // The mark blown up, so its geometry is judgeable.
                  const SizedBox(width: 96, height: 96, child: GoogleGlyph()),
                  const SizedBox(height: 20),
                  GoogleButton(onPressed: () {}),
                ],
              ),
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await expectLater(
      find.byType(Scaffold),
      matchesGoldenFile('goldens/google_button.png'),
    );
  });
}
