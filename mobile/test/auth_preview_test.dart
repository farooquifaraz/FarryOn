@Tags(['preview'])
library;

import 'package:farryon/core/theme.dart';
import 'package:farryon/features/auth/widgets/auth_bits.dart';
import 'package:farryon/features/auth/widgets/auth_scaffold.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// Renders the auth screens to PNGs so the design can actually be LOOKED at:
///   flutter test --run-skipped --update-goldens test/auth_preview_test.dart
/// then open test/goldens/*.png.
///
/// Not a regression gate — skipped by default and gitignored, because golden
/// bytes differ per host and would fail in CI for no useful reason. This
/// exists to review the design, and it earns its keep: it's how the field
/// fill, the button glow and a 9px overflow were all caught before shipping.
void main() {
  Future<void> shot(
    WidgetTester tester,
    String name,
    Widget child, {
    Size size = const Size(1080, 2200),
  }) async {
    tester.view.physicalSize = size;
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(MaterialApp(theme: Aurora.theme(), home: child));
    await tester.pumpAndSettle();

    await expectLater(
      find.byType(AuthScaffold),
      matchesGoldenFile('goldens/$name.png'),
    );
  }

  testWidgets('splash preview', (tester) async {
    await shot(
      tester,
      'splash_screen',
      AuthScaffold(
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
                    AuthCtaButton(label: 'Sign In', onPressed: () {}),
                    const SizedBox(height: 12),
                    AuthWhiteButton(label: 'Create Account', onPressed: () {}),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  });

  testWidgets('login preview', (tester) async {
    await shot(
      tester,
      'login_screen',
      AuthScaffold(
        child: AuthForm(
          children: [
            const Center(child: AuthLogo(size: 108)),
            const SizedBox(height: 18),
            const AuthField(label: 'Email', hint: 'Your email'),
            const SizedBox(height: 14),
            AuthField(
              label: 'Password',
              hint: 'Password',
              obscureText: true,
              suffix: IconButton(
                icon: const Icon(Icons.visibility_rounded,
                    size: 19, color: Aurora.authTextFaint),
                onPressed: () {},
              ),
            ),
            const SizedBox(height: 20),
            AuthCtaButton(label: 'Sign In', onPressed: () {}),
            const AuthDivider(),
            GoogleButton(onPressed: () {}),
            const SizedBox(height: 22),
            const Wrap(
              alignment: WrapAlignment.center,
              children: [
                Text("Don't have an account? ",
                    style: TextStyle(
                        fontSize: 12.5,
                        fontWeight: FontWeight.w600,
                        color: Aurora.authTextDim)),
                Text('Sign Up',
                    style: TextStyle(
                        fontSize: 12.5,
                        fontWeight: FontWeight.w700,
                        color: Aurora.neon)),
              ],
            ),
          ],
        ),
      ),
    );
  });

  testWidgets('google glyph closeup', (tester) async {
    await shot(
      tester,
      'google_button',
      AuthScaffold(
        child: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const SizedBox(width: 96, height: 96, child: GoogleGlyph()),
                const SizedBox(height: 20),
                GoogleButton(onPressed: () {}),
              ],
            ),
          ),
        ),
      ),
      size: const Size(1080, 900),
    );
  });
}
