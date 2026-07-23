import 'package:farryon/data/data_api.dart';
import 'package:farryon/features/settings/subscription_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// Settings → Subscription: the rendering rules the screen must get right.
///
/// SubscriptionView is pure (data in, widgets out), so these run without a
/// backend. The cases that matter: caps as honest text (-1 = unlimited, 0 =
/// not included), the missing-Stripe state saying "coming soon" instead of a
/// dead button, and never offering the plan you're already on (the server
/// enforces that; the view just mustn't invent rows).
void main() {
  SubscriptionOverview overview({
    String plan = 'free',
    int priceCents = 0,
    Map<String, UsageMeter> usage = const {
      'voice_seconds': UsageMeter(used: 60, cap: 180),
      'image_scans': UsageMeter(used: 1, cap: 2),
    },
    List<PlanOffer> upgrades = const [
      PlanOffer(name: 'plus', priceCents: 999),
      PlanOffer(name: 'pro', priceCents: 1999),
    ],
    bool checkoutAvailable = true,
  }) =>
      SubscriptionOverview(
        plan: plan,
        priceCents: priceCents,
        usage: usage,
        upgrades: upgrades,
        checkoutAvailable: checkoutAvailable,
      );

  Future<void> pump(
    WidgetTester tester,
    SubscriptionOverview o, {
    void Function(String)? onUpgrade,
  }) =>
      tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: SubscriptionView(overview: o, onUpgrade: onUpgrade ?? (_) {}),
        ),
      ));

  group('the plan card', () {
    testWidgets('free reads as Free, not as \$0.00', (tester) async {
      await pump(tester, overview());

      expect(find.text('Free plan'), findsOneWidget);
      expect(find.text('Free'), findsOneWidget);
      expect(find.textContaining('\$0.00'), findsNothing);
    });

    testWidgets('a paid plan shows its real price', (tester) async {
      await pump(
        tester,
        overview(plan: 'pro', priceCents: 1999, upgrades: const []),
      );

      expect(find.text('Pro plan'), findsOneWidget);
      expect(find.text('\$19.99 / month'), findsOneWidget);
    });
  });

  group('usage rows', () {
    testWidgets('voice reads in minutes, others in counts', (tester) async {
      await pump(tester, overview());

      expect(find.text('1 of 3 min used'), findsOneWidget);
      expect(find.text('1 of 2 used'), findsOneWidget);
    });

    testWidgets('a -1 cap reads Unlimited with no meter bar', (tester) async {
      await pump(
        tester,
        overview(
          plan: 'pro',
          priceCents: 1999,
          usage: const {'image_scans': UsageMeter(used: 40, cap: -1)},
          upgrades: const [],
        ),
      );

      expect(find.text('Unlimited'), findsOneWidget);
      expect(find.byType(LinearProgressIndicator), findsNothing);
    });

    testWidgets('a 0 cap reads not-included, not "0 of 0"', (tester) async {
      await pump(
        tester,
        overview(usage: const {'web_searches': UsageMeter(used: 0, cap: 0)}),
      );

      expect(find.text('Not included in this plan'), findsOneWidget);
      expect(find.textContaining('0 of 0'), findsNothing);
    });

    testWidgets('used seconds round UP so 1s never reads as 0 min',
        (tester) async {
      // Same honesty rule as the quota message: a meter that says "0 of 3 min"
      // an instant before the cap ends your session is lying.
      await pump(
        tester,
        overview(usage: const {'voice_seconds': UsageMeter(used: 1, cap: 180)}),
      );

      expect(find.text('1 of 3 min used'), findsOneWidget);
    });
  });

  group('upgrades', () {
    testWidgets('tapping an offer hands over its plan name', (tester) async {
      String? picked;
      await pump(tester, overview(), onUpgrade: (p) => picked = p);

      await tester.tap(find.textContaining('Plus —'));
      expect(picked, 'plus');
    });

    testWidgets('without Stripe keys the buttons say coming soon and are dead',
        (tester) async {
      var taps = 0;
      await pump(
        tester,
        overview(checkoutAvailable: false),
        onUpgrade: (_) => taps++,
      );

      expect(find.text('Coming soon'), findsNWidgets(2));
      expect(find.textContaining("aren't switched on yet"), findsOneWidget);
      await tester.tap(find.textContaining('Plus —'));
      expect(taps, 0, reason: 'a dead button must not pretend to work');
    });

    testWidgets('no upgrades section on the top plan', (tester) async {
      await pump(
        tester,
        overview(plan: 'pro', priceCents: 1999, upgrades: const []),
      );

      expect(find.text('Upgrade'), findsNothing);
    });
  });

  group('parsing', () {
    test('the wire shape round-trips', () {
      final o = SubscriptionOverview.fromJson(const {
        'plan': 'plus',
        'price_cents': 999,
        'currency': 'USD',
        'usage': {
          'voice_seconds': {'used': 30, 'cap': 420},
          'image_scans': {'used': 0, 'cap': -1},
        },
        'upgrades': [
          {'name': 'pro', 'price_cents': 1999},
        ],
        'checkout_available': false,
      });

      expect(o.plan, 'plus');
      expect(o.usage['voice_seconds']!.cap, 420);
      expect(o.usage['image_scans']!.unlimited, isTrue);
      expect(o.upgrades.single.name, 'pro');
      expect(o.checkoutAvailable, isFalse);
    });

    test('an empty payload degrades to a harmless free view', () {
      final o = SubscriptionOverview.fromJson(const {});
      expect(o.plan, 'free');
      expect(o.usage, isEmpty);
      expect(o.checkoutAvailable, isFalse);
    });
  });
}
