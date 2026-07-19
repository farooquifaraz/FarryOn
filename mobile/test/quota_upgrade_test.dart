import 'package:farryon/core/config.dart';
import 'package:farryon/data/data_api.dart';
import 'package:farryon/features/live/live_screen.dart';
import 'package:farryon/state/live_state.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// Phase 4: hitting the daily cap is a clear, actionable end — not a silent cut.
///
/// With quota enforcement on, a user with no subscription runs out of voice
/// minutes and the backend ends the session with a `quota_exceeded` error. The
/// old path turned any server error into a generic banner and a bare "Session
/// ended" overlay, which reads like a fault. This is the opposite: say why, and
/// offer the one thing that helps — Upgrade.
void main() {
  const config = AppConfig(host: 'x', port: 8000, secure: false, authToken: 't');

  group('the checkout API', () {
    DataApi apiThat(http.Response Function(http.Request) respond) => DataApi(
          config,
          client: MockClient((req) async => respond(req)),
        );

    test('returns the hosted URL from the envelope', () async {
      final api = apiThat((req) {
        expect(req.url.path, '/api/v1/billing/checkout');
        return http.Response(
          '{"success":true,"data":{"url":"https://checkout.stripe/go"}}',
          200,
        );
      });

      expect(await api.createCheckout('plus'), 'https://checkout.stripe/go');
    });

    test('a 503 (Stripe not configured) is a null, not a throw', () async {
      // The button must be able to say "not available yet" rather than crash on
      // a user who tapped Upgrade before keys were wired.
      final api = apiThat((_) => http.Response('{}', 503));

      expect(await api.createCheckout('plus'), isNull);
    });

    test('a dead session still propagates, so the app can sign out', () async {
      final api = apiThat((_) => http.Response('{}', 401));

      expect(api.createCheckout('plus'),
          throwsA(isA<SessionExpiredException>()));
    });
  });

  group('the reconnect overlay at the cap', () {
    Future<void> pump(
      WidgetTester tester, {
      required bool capReached,
      VoidCallback? onUpgrade,
      VoidCallback? onReconnect,
    }) =>
        tester.pumpWidget(MaterialApp(
          home: Scaffold(
            body: ReconnectOverlay(
              capReached: capReached,
              onUpgrade: onUpgrade ?? () {},
              onReconnect: onReconnect ?? () {},
            ),
          ),
        ));

    testWidgets('at the cap it leads with Upgrade and explains why',
        (tester) async {
      await pump(tester, capReached: true);

      expect(find.text('Upgrade'), findsOneWidget);
      expect(find.textContaining('free minutes'), findsOneWidget);
      // The bare fault wording must NOT be the headline here.
      expect(find.text('Session ended'), findsNothing);
    });

    testWidgets('a normal end shows Start session, not Upgrade',
        (tester) async {
      await pump(tester, capReached: false);

      expect(find.text('Session ended'), findsOneWidget);
      expect(find.text('Start session'), findsOneWidget);
      expect(find.text('Upgrade'), findsNothing);
    });

    testWidgets('tapping Upgrade calls the handler', (tester) async {
      var upgrades = 0;
      await pump(tester, capReached: true, onUpgrade: () => upgrades++);

      await tester.tap(find.text('Upgrade'));
      expect(upgrades, 1);
    });

    testWidgets('Start-again is still available at the cap — a new day may have '
        'ticked over', (tester) async {
      var reconnects = 0;
      await pump(tester, capReached: true, onReconnect: () => reconnects++);

      await tester.tap(find.text('Try starting again'));
      expect(reconnects, 1);
    });
  });

  group('state', () {
    test('capReached defaults off and survives copyWith', () {
      const s = LiveSessionState();
      expect(s.capReached, isFalse);
      expect(s.copyWith(capReached: true).capReached, isTrue);
      // An unrelated copyWith must not silently reset it.
      expect(
        s.copyWith(capReached: true).copyWith(micOpen: true).capReached,
        isTrue,
      );
    });
  });
}
