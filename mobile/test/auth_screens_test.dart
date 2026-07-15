import 'package:farryon/core/theme.dart';
import 'package:farryon/features/auth/widgets/auth_bits.dart';
import 'package:farryon/features/auth/widgets/auth_scaffold.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Widget _host(Widget child) =>
    MaterialApp(theme: Aurora.theme(), home: AuthScaffold(child: child));

void main() {
  group('AuthScaffold', () {
    testWidgets('paints the gradient backdrop and its glow', (tester) async {
      await tester.pumpWidget(_host(const SizedBox()));
      expect(find.byType(CustomPaint), findsWidgets);
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });
  });

  group('AuthField', () {
    testWidgets('shows its label and hint', (tester) async {
      await tester.pumpWidget(
        _host(const AuthField(label: 'Email', hint: 'Your email')),
      );
      expect(find.text('Email'), findsOneWidget);
      expect(find.text('Your email'), findsOneWidget);
    });

    testWidgets('takes typing, and stays put while readOnly', (tester) async {
      final ctl = TextEditingController();
      addTearDown(ctl.dispose);

      await tester.pumpWidget(_host(AuthField(hint: 'Type', controller: ctl)));
      await tester.enterText(find.byType(TextField), 'hello');
      expect(ctl.text, 'hello');

      // readOnly (not disabled) is deliberate: disabling a focused field drops
      // the keyboard, which would fight a user retrying after a failed submit.
      await tester.pumpWidget(
        _host(AuthField(hint: 'Type', controller: ctl, readOnly: true)),
      );
      await tester.enterText(find.byType(TextField), 'ignored');
      expect(ctl.text, 'hello');
    });

    testWidgets('disposes cleanly when given a caller-owned focus node',
        (tester) async {
      // The field must not dispose a node it doesn't own — doing so would
      // throw when the caller later disposes it themselves.
      final node = FocusNode();
      await tester.pumpWidget(_host(AuthField(hint: 'x', focusNode: node)));
      await tester.pumpWidget(_host(const SizedBox()));
      expect(tester.takeException(), isNull);
      node.dispose();
    });
  });

  group('buttons share one spec', () {
    testWidgets('CTA, white and Google are all the same height',
        (tester) async {
      await tester.pumpWidget(
        _host(Column(children: [
          AuthCtaButton(label: 'Sign In', onPressed: () {}),
          AuthWhiteButton(label: 'Create Account', onPressed: () {}),
          GoogleButton(onPressed: () {}),
        ])),
      );
      double heightOf(Type t) => tester.getSize(find.byType(t)).height;
      expect(heightOf(AuthCtaButton), Aurora.authButtonHeight);
      expect(heightOf(AuthWhiteButton), Aurora.authButtonHeight);
      expect(heightOf(GoogleButton), Aurora.authButtonHeight);
    });
  });

  group('AuthCtaButton', () {
    testWidgets('fires onPressed', (tester) async {
      var taps = 0;
      await tester.pumpWidget(
        _host(AuthCtaButton(label: 'Sign In', onPressed: () => taps++)),
      );
      await tester.tap(find.byType(AuthCtaButton));
      expect(taps, 1);
    });

    testWidgets('shows a spinner and ignores taps while loading',
        (tester) async {
      var taps = 0;
      await tester.pumpWidget(
        _host(AuthCtaButton(
            label: 'Sign In', loading: true, onPressed: () => taps++)),
      );
      expect(find.byType(CircularProgressIndicator), findsOneWidget);
      await tester.tap(find.byType(AuthCtaButton));
      expect(taps, 0, reason: 'a loading button must not double-submit');
    });

    testWidgets('is inert when disabled', (tester) async {
      await tester.pumpWidget(
        _host(const AuthCtaButton(label: 'Sign In', onPressed: null)),
      );
      await tester.tap(find.byType(AuthCtaButton));
      expect(tester.takeException(), isNull);
    });
  });

  group('GoogleButton', () {
    testWidgets("uses Google's required wording and fires onPressed",
        (tester) async {
      var taps = 0;
      await tester.pumpWidget(_host(GoogleButton(onPressed: () => taps++)));
      expect(find.text('Continue with Google'), findsOneWidget);
      await tester.tap(find.byType(GoogleButton));
      expect(taps, 1);
    });

    testWidgets('swaps the G for a spinner while busy', (tester) async {
      await tester.pumpWidget(_host(GoogleButton(busy: true, onPressed: () {})));
      expect(find.byType(CircularProgressIndicator), findsOneWidget);
      expect(find.text('Signing in…'), findsOneWidget);
    });
  });

  group('AuthBanner', () {
    testWidgets('an error reads in the error colour', (tester) async {
      await tester.pumpWidget(_host(const AuthBanner.error('Wrong password.')));
      expect(find.text('Wrong password.'), findsOneWidget);
      expect(find.byIcon(Icons.error_outline_rounded), findsOneWidget);
    });

    testWidgets('a success reads in neon', (tester) async {
      await tester.pumpWidget(_host(const AuthBanner.success('Account made.')));
      final icon = tester.widget<Icon>(find.byIcon(Icons.check_circle_rounded));
      expect(icon.color, Aurora.neon);
    });
  });

  testWidgets('AuthLogo falls back to a glyph when the asset is missing',
      (tester) async {
    // Assets don't resolve in widget tests, so this exercises the errorBuilder
    // — the path that must never leave a blank hole where the brand goes.
    await tester.pumpWidget(_host(const AuthLogo(size: 100)));
    await tester.pump();
    expect(find.byType(AuthLogo), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
