import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

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
      padding: const EdgeInsets.all(16),
      children: [
        // ---- Current plan ------------------------------------------------
        SettingsGroup(children: [
          SettingsRow(
            icon: Icons.workspace_premium_rounded,
            gradient: o.plan == 'free' ? Aurora.gradTeal : Aurora.gradAmber,
            title: '${_title(o.plan)} plan',
            subtitle: o.priceCents == 0
                ? 'Free'
                : '\$${(o.priceCents / 100).toStringAsFixed(2)} / month',
            trailing: const SizedBox.shrink(),
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
        if (o.upgrades.isNotEmpty) ...[
          const SectionLabel('Upgrade'),
          SettingsGroup(children: [
            for (final (i, p) in o.upgrades.indexed)
              SettingsRow(
                icon: Icons.arrow_circle_up_rounded,
                gradient: Aurora.gradGreen,
                title: '${_title(p.name)} — '
                    '\$${(p.priceCents / 100).toStringAsFixed(2)}/mo',
                subtitle: o.checkoutAvailable
                    ? 'Tap to upgrade'
                    : 'Coming soon',
                onTap:
                    o.checkoutAvailable ? () => onUpgrade(p.name) : null,
                showDivider: i < o.upgrades.length - 1,
              ),
          ]),
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
