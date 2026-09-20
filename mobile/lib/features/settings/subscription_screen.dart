import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/money.dart';
import '../../core/theme.dart';
import '../../core/ui.dart';
import '../../data/data_api.dart';
import '../../state/providers.dart';

/// Settings → Subscription: the plan you're on, this month's usage against
/// its caps, and the plans you could move to.
///
/// Fetches once on open (usage flushes server-side every ~15s of speech, so a
/// live ticker would be false precision) and hands the result to
/// [SubscriptionView], which is a pure widget so the rendering rules — caps,
/// unlimited, the missing-keys state — are testable without a backend.
class SubscriptionScreen extends ConsumerStatefulWidget {
  const SubscriptionScreen({super.key});

  static void open(BuildContext context) => Navigator.of(context).push(
        MaterialPageRoute<void>(builder: (_) => const SubscriptionScreen()),
      );

  @override
  ConsumerState<SubscriptionScreen> createState() => _SubscriptionScreenState();
}

class _SubscriptionScreenState extends ConsumerState<SubscriptionScreen> {
  SubscriptionOverview? _overview;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final overview = await ref.read(dataApiProvider).subscription();
      if (mounted) setState(() => _overview = overview);
    } on SessionExpiredException {
      if (mounted) setState(() => _error = 'Please sign in again.');
    } catch (_) {
      if (mounted) {
        setState(() => _error = "Couldn't load your plan — try again.");
      }
    }
  }

  Future<void> _upgrade(String plan) async {
    final problem = await ref.read(liveProvider.notifier).startUpgrade(plan);
    if (problem != null && mounted) {
      ScaffoldMessenger.of(context)
        ..clearSnackBars()
        ..showSnackBar(SnackBar(content: Text(problem)));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Aurora.base,
      appBar: AppBar(
        backgroundColor: Aurora.base,
        title: const Text('Subscription',
            style: TextStyle(color: Aurora.textPrimary)),
        iconTheme: const IconThemeData(color: Aurora.textPrimary),
      ),
      body: _error != null
          ? Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(_error!, style: const TextStyle(color: Aurora.textMuted)),
                  const SizedBox(height: 12),
                  TextButton(
                    onPressed: () {
                      setState(() => _error = null);
                      _load();
                    },
                    child: const Text('Retry'),
                  ),
                ],
              ),
            )
          : _overview == null
              ? const Center(
                  child: CircularProgressIndicator(color: Aurora.teal))
              : SubscriptionView(overview: _overview!, onUpgrade: _upgrade),
    );
  }
}

/// Pure rendering of a [SubscriptionOverview] — no network, fully testable.
class SubscriptionView extends StatelessWidget {
  const SubscriptionView({
    super.key,
    required this.overview,
    required this.onUpgrade,
  });

  final SubscriptionOverview overview;
  final void Function(String plan) onUpgrade;

  @override
  Widget build(BuildContext context) {
    final o = overview;
    final trial = o.window == 'lifetime';
    // Recorded so the user can see WHERE the minutes went, never as a cap.
    final translateUsed = o.usage['translate_seconds']?.used ?? 0;
    final shown = [
      for (final e in o.usage.entries)
        if (e.key != 'translate_seconds') e,
    ];
    return ListView(
      // Edge-to-edge: the last plan row must clear the system navigation bar.
      padding: EdgeInsets.fromLTRB(
          16, 16, 16, 16 + MediaQuery.paddingOf(context).bottom),
      children: [
        // ---- Current plan ------------------------------------------------
        SettingsGroup(children: [
          SettingsRow(
            icon: Icons.workspace_premium_rounded,
            gradient: o.plan == 'free' ? Aurora.gradTeal : Aurora.gradAmber,
            title: '${o.planTitle.isEmpty ? _title(o.plan) : o.planTitle} plan',
            subtitle: _currentPlanLine(o),
            subtitleColor: (o.daysLeft ?? 99) <= 3 ? Aurora.amber : null,
            trailing: const SizedBox.shrink(),
            showDivider: o.oneTime && o.priceCents > 0,
          ),
          // A plan bought for a period is extended by buying it again — the
          // next period starts when this one ends, so buying early loses
          // nothing. Offered here, under the plan it extends, not in the
          // upgrade list where it would read as a change of plan.
          if (o.oneTime && o.priceCents > 0)
            SettingsRow(
              icon: Icons.autorenew_rounded,
              gradient: Aurora.gradGreen,
              title: 'Buy another ${_period(o.plan)} — '
                  '${formatMoney(o.priceCents, o.currency)}',
              subtitle: !o.checkoutAvailable
                  ? 'Coming soon'
                  : 'Adds ${_period(o.plan)} after '
                      '${o.periodEnd == null ? 'the current period' : _date(o.periodEnd!)}',
              onTap: o.checkoutAvailable ? () => onUpgrade(o.plan) : null,
              showDivider: false,
            ),
        ]),
        const SizedBox(height: 20),

        // ---- Usage -------------------------------------------------------
        // `translate_seconds` is deliberately NOT a row of its own. It shares
        // the talk budget, so listing it beside voice with the same cap read
        // as a second allowance — the exact confusion this screen exists to
        // prevent. It appears as a sub-line under Talk time instead.
        SectionLabel(trial ? 'Your free trial' : "This month's usage"),
        SettingsGroup(children: [
          for (final (i, e) in shown.indexed)
            _UsageRow(
              metric: e.key,
              meter: e.value,
              trial: trial,
              translationSeconds:
                  e.key == 'voice_seconds' ? translateUsed : null,
              showDivider: i < shown.length - 1,
            ),
        ]),
        Padding(
          padding: const EdgeInsets.fromLTRB(4, 10, 4, 0),
          child: Text(
            trial
                ? 'Talk time covers both talking to Farry and live '
                    'translation — they share one budget. Your free trial is a '
                    'one-time allowance, so it does not reset each month.'
                : 'Talk time covers both talking to Farry and live '
                    'translation — they share one budget. Everything here '
                    'resets on the 1st of each month.',
            style: const TextStyle(
                color: Aurora.textMuted, fontSize: 12, height: 1.45),
          ),
        ),
        const SizedBox(height: 20),

        // ---- Upgrades ----------------------------------------------------
        // Plans come as monthly and yearly twins; the picker shows one card
        // per tier with a Monthly / Annual switch above, the way the
        // website does, instead of one row per plan.
        if (o.upgrades.isNotEmpty) ...[
          const SectionLabel('Upgrade'),
          PlanPicker(
            offers: o.upgrades,
            checkoutAvailable: o.checkoutAvailable,
            onChoose: onUpgrade,
          ),
          if (!o.checkoutAvailable)
            const Padding(
              padding: EdgeInsets.fromLTRB(4, 10, 4, 0),
              child: Text(
                "Payments aren't switched on yet — upgrades will open here "
                'once they are.',
                style: TextStyle(color: Aurora.textMuted, fontSize: 12),
              ),
            ),
        ],
      ],
    );
  }

  static String _title(String s) =>
      s.isEmpty ? s : s[0].toUpperCase() + s.substring(1);

  /// "30 days" or "12 months" — the period a one-time plan buys.
  static String _period(String plan) =>
      plan.endsWith('_yearly') ? '12 months' : '30 days';

  static const _months = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];

  static String _date(DateTime d) {
    final l = d.toLocal();
    return '${l.day} ${_months[l.month - 1]} ${l.year}';
  }

  /// The line under the plan's name: its price and, for a plan bought for a
  /// period, when that period ends — with the days left once it is close.
  static String _currentPlanLine(SubscriptionOverview o) {
    if (o.priceCents == 0) return 'Free';
    final price = formatMoney(o.priceCents, o.currency);
    if (!o.oneTime) {
      return '$price / ${o.plan.endsWith('_yearly') ? 'year' : 'month'}';
    }
    final end = o.periodEnd;
    if (end == null) return '$price for ${_period(o.plan)}';
    final left = o.daysLeft ?? 0;
    final when = left <= 0
        ? 'ends today'
        : left <= 3
            ? 'ends in $left day${left == 1 ? '' : 's'} — buy again to keep it'
            : 'valid till ${_date(end)}';
    return '$price for ${_period(o.plan)} · $when';
  }
}

class _UsageRow extends StatelessWidget {
  const _UsageRow({
    required this.metric,
    required this.meter,
    required this.showDivider,
    this.trial = false,
    this.translationSeconds,
  });

  /// Trial allowances are one-time, so the row must not say "this month".
  final bool trial;

  /// Seconds of the talk budget spent on live translation — shown under the
  /// Talk time row so the number is explained rather than merely capped.
  /// Null on every other row.
  final int? translationSeconds;

  final String metric;
  final UsageMeter meter;
  final bool showDivider;

  static const _labels = {
    // One budget covers talking to Farry AND live translation — they run
    // through the same model at the same price, so calling it "voice" hid
    // half of what spends it (repriced 2026-09-05).
    'voice_seconds': 'Talk time (voice + translation)',
    'image_scans': 'Image scans',
    'web_searches': 'Web searches',
  };

  static const _icons = {
    'voice_seconds': Icons.mic_rounded,
    'image_scans': Icons.image_search_rounded,
    'web_searches': Icons.travel_explore_rounded,
  };

  @override
  Widget build(BuildContext context) {
    final isVoice = metric == 'voice_seconds';
    final String subtitle;
    if (meter.unlimited) {
      subtitle = 'Unlimited';
    } else if (meter.cap == 0) {
      subtitle = 'Not included in this plan';
    } else {
      // "this month" is the truth on a paid plan; a trial's allowance is
      // one-time, and calling it monthly is a promise of a reset that never
      // comes.
      final when = trial ? 'used in total' : 'used this month';
      if (isVoice) {
        // Voice is stored in seconds but people think in minutes. Round used
        // UP so "1 second spent" never reads as "0 of 3 min" right before the
        // cap ends a session — the same honesty rule as the quota message.
        final usedMin = (meter.used / 60).ceil();
        subtitle =
            '${meter.used == 0 ? 0 : usedMin} of ${meter.cap ~/ 60} min $when';
      } else {
        subtitle = '${meter.used} of ${meter.cap} $when';
      }
    }

    // What the talk minutes actually went on. Only worth saying once there
    // is something to say — a "0 min of it was translation" line is noise.
    final translation = translationSeconds ?? 0;
    final breakdown = translation > 0
        ? 'including ${(translation / 60).ceil()} min of live translation'
        : null;

    return SettingsRow(
      icon: _icons[metric] ?? Icons.data_usage_rounded,
      gradient: Aurora.gradBlue,
      title: _labels[metric] ?? metric,
      subtitle: breakdown == null ? subtitle : '$subtitle\n$breakdown',
      trailing: meter.unlimited || meter.cap == 0
          ? const SizedBox.shrink()
          : SizedBox(
              width: 52,
              child: LinearProgressIndicator(
                value: (meter.used / meter.cap).clamp(0.0, 1.0),
                minHeight: 5,
                borderRadius: BorderRadius.circular(3),
                backgroundColor: Colors.white.withValues(alpha: 0.10),
                color: meter.used >= meter.cap ? Aurora.amber : Aurora.teal,
              ),
            ),
      showDivider: showDivider,
    );
  }
}


/// One card per tier with a Monthly / Annual switch — the website's pricing
/// section, on the phone. The yearly card says what the year saves against
/// twelve months, because that is the only reason to pick it.
class PlanPicker extends StatefulWidget {
  const PlanPicker({
    super.key,
    required this.offers,
    required this.checkoutAvailable,
    required this.onChoose,
  });

  final List<PlanOffer> offers;
  final bool checkoutAvailable;
  final void Function(String plan) onChoose;

  @override
  State<PlanPicker> createState() => _PlanPickerState();
}

class _PlanPickerState extends State<PlanPicker> {
  bool _yearly = false;

  /// Tiers in the order the backend listed them, each with its monthly and
  /// yearly offer (either may be missing).
  List<_Tier> get _tiers {
    final byTier = <String, _Tier>{};
    for (final p in widget.offers) {
      final t = byTier.putIfAbsent(p.tier, () => _Tier(p.tier));
      if (p.yearly) {
        t.yearly = p;
      } else {
        t.monthly = p;
      }
    }
    return byTier.values.toList();
  }

  @override
  Widget build(BuildContext context) {
    final tiers = _tiers;
    final hasYearly = tiers.any((t) => t.yearly != null);
    final hasMonthly = tiers.any((t) => t.monthly != null);
    final yearly = hasYearly && (_yearly || !hasMonthly);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (hasYearly && hasMonthly)
          Center(
            child: _IntervalSwitch(
              yearly: yearly,
              onChanged: (v) => setState(() => _yearly = v),
            ),
          ),
        const SizedBox(height: 14),
        for (final t in tiers)
          if ((yearly ? t.yearly : t.monthly) != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: _PlanCard(
                offer: (yearly ? t.yearly : t.monthly)!,
                monthly: t.monthly,
                enabled: widget.checkoutAvailable,
                onChoose: widget.onChoose,
              ),
            ),
      ],
    );
  }
}

class _Tier {
  _Tier(this.name);
  final String name;
  PlanOffer? monthly;
  PlanOffer? yearly;
}

class _IntervalSwitch extends StatelessWidget {
  const _IntervalSwitch({required this.yearly, required this.onChanged});

  final bool yearly;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    Widget seg(String label, bool value) {
      final on = yearly == value;
      return GestureDetector(
        onTap: () => onChanged(value),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 180),
          padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 8),
          decoration: BoxDecoration(
            color: on ? Aurora.teal : Colors.transparent,
            borderRadius: BorderRadius.circular(999),
          ),
          child: Text(
            label,
            style: TextStyle(
              color: on ? Aurora.tealInk : Aurora.textMuted,
              fontWeight: FontWeight.w600,
              fontSize: 13,
            ),
          ),
        ),
      );
    }

    return Container(
      padding: const EdgeInsets.all(4),
      decoration: BoxDecoration(
        color: Aurora.glass,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: Aurora.glassBorder),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [seg('Monthly', false), seg('Annual', true)],
      ),
    );
  }
}

class _PlanCard extends StatelessWidget {
  const _PlanCard({
    required this.offer,
    required this.monthly,
    required this.enabled,
    required this.onChoose,
  });

  /// The offer this card sells (monthly or yearly, per the switch).
  final PlanOffer offer;

  /// The tier's monthly twin, for the saving line on a yearly card.
  final PlanOffer? monthly;
  final bool enabled;
  final void Function(String plan) onChoose;

  /// Plus is the tier we point at, on every list (plus, plus_in, plus_ae).
  bool get _popular => offer.tier.startsWith('plus');

  String get _title {
    final t = offer.title.isEmpty
        ? offer.tier[0].toUpperCase() + offer.tier.substring(1)
        : offer.title;
    // The yearly twin's title carries "(yearly)"; the switch already says so.
    return t.replaceAll(RegExp(r'\s*\(yearly\)$'), '');
  }

  /// "per month" / "per year" — or, bought outright, "for 30 days" / "for
  /// 12 months": nothing renews, and the card must not imply it does.
  String get _period => offer.oneTime
      ? (offer.yearly ? 'for 12 months' : 'for 30 days')
      : (offer.yearly ? 'per year' : 'per month');

  /// What a year saves against twelve months of the monthly price, when
  /// both are known: "Save ₹288" — the amount alone, no percentage (Faraz).
  /// Null when there is nothing to say.
  String? get _saving {
    final m = monthly;
    if (!offer.yearly || m == null || m.priceCents <= 0) return null;
    final saved = m.priceCents * 12 - offer.priceCents;
    if (saved <= 0) return null;
    return 'Save ${formatMoney(saved, offer.currency)}';
  }

  @override
  Widget build(BuildContext context) {
    final saving = _saving;
    final perMonth = offer.yearly
        ? 'works out at ${formatMoney((offer.priceCents / 12).round(), offer.currency)}/mo'
        : null;
    final feats = <String>[
      if (offer.talkMinutes > 0) '${_n(offer.talkMinutes)} talk minutes a month',
      if (offer.imageScans > 0) '${_n(offer.imageScans)} image scans a month',
      if (offer.webSearches > 0) '${_n(offer.webSearches)} web searches a month',
      if (offer.oneTime) 'One-time payment · no auto-renew',
    ];
    final border = _popular ? Aurora.teal : Aurora.glassBorder;
    return Container(
      decoration: BoxDecoration(
        color: Aurora.surfaceHigh,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: border, width: _popular ? 1.4 : 1),
        boxShadow: _popular
            ? [
                BoxShadow(
                    color: Aurora.teal.withValues(alpha: 0.18),
                    blurRadius: 18,
                    spreadRadius: 1),
              ]
            : null,
      ),
      padding: const EdgeInsets.fromLTRB(18, 16, 18, 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(_title,
                    style: const TextStyle(
                        color: Aurora.textPrimary,
                        fontSize: 18,
                        fontWeight: FontWeight.w700)),
              ),
              if (_popular)
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: Aurora.teal.withValues(alpha: 0.16),
                    borderRadius: BorderRadius.circular(999),
                    border: Border.all(color: Aurora.teal.withValues(alpha: .5)),
                  ),
                  child: const Text('Most popular',
                      style: TextStyle(
                          color: Aurora.mint,
                          fontSize: 11,
                          fontWeight: FontWeight.w700)),
                ),
            ],
          ),
          const SizedBox(height: 10),
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(formatMoney(offer.priceCents, offer.currency),
                  style: const TextStyle(
                      color: Aurora.textPrimary,
                      fontSize: 30,
                      fontWeight: FontWeight.w800,
                      height: 1)),
              const SizedBox(width: 8),
              Padding(
                padding: const EdgeInsets.only(bottom: 3),
                child: Text(_period,
                    style: const TextStyle(
                        color: Aurora.textMuted, fontSize: 13)),
              ),
            ],
          ),
          if (perMonth != null || saving != null) ...[
            const SizedBox(height: 6),
            Wrap(
              spacing: 8,
              runSpacing: 4,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                if (perMonth != null)
                  Text(perMonth,
                      style: const TextStyle(
                          color: Aurora.textMuted, fontSize: 12)),
                if (saving != null)
                  Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: Aurora.amber.withValues(alpha: 0.14),
                      borderRadius: BorderRadius.circular(999),
                      border:
                          Border.all(color: Aurora.amber.withValues(alpha: .4)),
                    ),
                    child: Text(saving,
                        style: const TextStyle(
                            color: Aurora.amber,
                            fontSize: 11,
                            fontWeight: FontWeight.w700)),
                  ),
              ],
            ),
          ],
          if (feats.isNotEmpty) ...[
            const SizedBox(height: 12),
            for (final f in feats)
              Padding(
                padding: const EdgeInsets.only(bottom: 5),
                child: Row(
                  children: [
                    const Icon(Icons.check_rounded,
                        size: 15, color: Aurora.mint),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(f,
                          style: const TextStyle(
                              color: Aurora.textPrimary, fontSize: 13)),
                    ),
                  ],
                ),
              ),
          ],
          const SizedBox(height: 12),
          SizedBox(
            width: double.infinity,
            child: _popular
                ? FilledButton(
                    onPressed: enabled ? () => onChoose(offer.name) : null,
                    style: FilledButton.styleFrom(
                      backgroundColor: Aurora.teal,
                      foregroundColor: Aurora.tealInk,
                      padding: const EdgeInsets.symmetric(vertical: 12),
                    ),
                    child: Text(enabled ? 'Choose $_title' : 'Coming soon'),
                  )
                : OutlinedButton(
                    onPressed: enabled ? () => onChoose(offer.name) : null,
                    style: OutlinedButton.styleFrom(
                      foregroundColor: Aurora.mint,
                      side: BorderSide(color: Aurora.teal.withValues(alpha: .6)),
                      padding: const EdgeInsets.symmetric(vertical: 12),
                    ),
                    child: Text(enabled ? 'Choose $_title' : 'Coming soon'),
                  ),
          ),
        ],
      ),
    );
  }

  static String _n(int v) {
    final s = v.toString();
    if (s.length <= 3) return s;
    return '${s.substring(0, s.length - 3)},${s.substring(s.length - 3)}';
  }
}
