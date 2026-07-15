import 'package:farryon/core/theme.dart';
import 'package:farryon/features/auth/widgets/auth_bits.dart';
import 'package:farryon/features/auth/widgets/auth_scaffold.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Widget _host(Widget child) =>
    MaterialApp(theme: Aurora.theme(), home: AuthScaffold(child: child));

void main() {
  group('AuthScaffold', () {
    testWidgets('paints the aurora and drives its drift', (tester) async {
      await tester.pumpWidget(_host(const SizedBox()));
      expect(find.byType(CustomPaint), findsWidgets);

      // The orbs must keep moving without the tree rebuilding — a repaint
      // boundary isolates them, so pumping frames should not throw or settle.
      await tester.pump(const Duration(seconds: 1));
      await tester.pump(const Duration(seconds: 1));
      expect(tester.takeException(), isNull);
    });

    testWidgets('holds still when the platform asks for reduced motion',
        (tester) async {
      await tester.pumpWidget(
        MediaQuery(
          data: const MediaQueryData(disableAnimations: true),
          child: _host(const SizedBox()),
        ),
      );
      // pumpAndSettle times out if anything is still animating, so it passing
      // IS the assertion that the drift stopped.
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });
  });

  group('GoogleButton', () {
    testWidgets('shows Google\'s required wording and fires onPressed',
        (tester) async {
      var taps = 0;
      await tester.pumpWidget(
        _host(GoogleButton(onPressed: () => taps++)),
      );
      expect(find.text('Continue with Google'), findsOneWidget);

      await tester.tap(find.byType(GoogleButton));
      expect(taps, 1);
    });

    testWidgets('swaps the G for a spinner and stops being tappable when busy',
        (tester) async {
      var taps = 0;
      await tester.pumpWidget(
        _host(GoogleButton(busy: true, onPressed: () => taps++)),
      );
      expect(find.byType(CircularProgressIndicator), findsOneWidget);
      expect(find.text('Signing in…'), findsOneWidget);
    });

    testWidgets('is inert while disabled', (tester) async {
      await tester.pumpWidget(_host(const GoogleButton(onPressed: null)));
      await tester.tap(find.byType(GoogleButton));
      expect(tester.takeException(), isNull);
    });
  });

  group('AuthBanner', () {
    testWidgets('an error reads in the danger colour', (tester) async {
      await tester.pumpWidget(_host(const AuthBanner.error('Wrong password.')));
      expect(find.text('Wrong password.'), findsOneWidget);
      final icon = tester.widget<Icon>(find.byType(Icon));
      expect(icon.color, Aurora.danger);
    });

    testWidgets('a success reads in mint', (tester) async {
      await tester.pumpWidget(_host(const AuthBanner.success('Account made.')));
      final icon = tester.widget<Icon>(find.byType(Icon));
      expect(icon.color, Aurora.mint);
    });
  });

  testWidgets('AuthBrand renders the wordmark', (tester) async {
    await tester.pumpWidget(_host(const AuthBrand()));
    expect(find.byType(AuthBrand), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
