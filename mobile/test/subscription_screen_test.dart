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
    String window = 'month',
    String planTitle = '',
  }) =>
      SubscriptionOverview(
        plan: plan,
        priceCents: priceCents,
        usage: usage,
        upgrades: upgrades,
        checkoutAvailable: checkoutAvailable,
        window: window,
        planTitle: planTitle,
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

      expect(find.text('1 of 3 min used this month'), findsOneWidget);
      expect(find.text('1 of 2 used this month'), findsOneWidget);
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

      expect(find.text('1 of 3 min used this month'), findsOneWidget);
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

  group('one talk budget, said once', () {
    testWidgets('translation is not a second meter with its own cap',
        (tester) async {
      // The server sends translate_seconds with the SAME cap as voice — they
      // share a budget. Rendered as a row it would read as a second
      // allowance, which is the confusion this screen has to prevent.
      await pump(
        tester,
        overview(usage: const {
          'voice_seconds': UsageMeter(used: 600, cap: 30000),
          'translate_seconds': UsageMeter(used: 300, cap: 30000),
        }),
      );

      expect(find.text('translate_seconds'), findsNothing);
      expect(find.textContaining('of 500 min used this month'), findsOneWidget);
      expect(
        find.textContaining('including 5 min of live translation'),
        findsOneWidget,
      );
    });

    testWidgets('no translation line when none was used', (tester) async {
      await pump(
        tester,
        overview(usage: const {
          'voice_seconds': UsageMeter(used: 60, cap: 30000),
          'translate_seconds': UsageMeter(used: 0, cap: 30000),
        }),
      );

      expect(find.textContaining('including'), findsNothing);
    });

    testWidgets('a trial never promises a monthly reset', (tester) async {
      await pump(tester, overview(window: 'lifetime'));

      expect(find.text('YOUR FREE TRIAL'), findsOneWidget);
      expect(find.textContaining('used in total'), findsWidgets);
      expect(find.textContaining('does not reset each month'), findsOneWidget);
      expect(find.textContaining("THIS MONTH"), findsNothing);
    });
  });

  group('yearly plans', () {
    testWidgets('a yearly upgrade shows its real price per YEAR', (tester) async {
      // /mo on a $165 yearly price would read as a tenfold price rise.
      await pump(
        tester,
        overview(upgrades: const [
          PlanOffer(
            name: 'plus_yearly',
            priceCents: 16500,
            title: 'Plus (yearly)',
            interval: 'year',
          ),
        ]),
      );

      expect(find.textContaining('Plus (yearly) — \$165.00/yr'), findsOneWidget);
      expect(find.textContaining('/mo'), findsNothing);
      expect(find.textContaining('cheaper than monthly'), findsOneWidget);
    });

    testWidgets('the current plan card names the year, not the key',
        (tester) async {
      await pump(
        tester,
        overview(
          plan: 'pro_yearly',
          planTitle: 'Pro (yearly)',
          priceCents: 27500,
          upgrades: const [],
        ),
      );

      expect(find.text('Pro (yearly) plan'), findsOneWidget);
      expect(find.text('\$275.00 / year'), findsOneWidget);
      expect(find.textContaining('pro_yearly'), findsNothing);
    });
  });
}
