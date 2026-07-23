import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme.dart';
import '../../core/ui.dart';
import '../../data/data_api.dart';
import '../../state/providers.dart';

/// Settings → Subscription: the plan you're on, today's usage against its
/// caps, and the plans you could move to.
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

        // ---- Today's usage ----------------------------------------------
        const SectionLabel("Today's usage"),
        SettingsGroup(children: [
          for (final (i, e) in o.usage.entries.indexed)
            _UsageRow(
              metric: e.key,
              meter: e.value,
              showDivider: i < o.usage.length - 1,
            ),
        ]),
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
  });

  final String metric;
  final UsageMeter meter;
  final bool showDivider;

  static const _labels = {
    'voice_seconds': 'Voice time',
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
    } else if (isVoice) {
      // Voice is stored in seconds but people think in minutes. Round used
      // UP so "1 second spent" never reads as "0 of 3 min" right before the
      // cap ends a session — the same honesty rule as the quota message.
      final usedMin = (meter.used / 60).ceil();
      subtitle = '${meter.used == 0 ? 0 : usedMin} of ${meter.cap ~/ 60} min used';
    } else {
      subtitle = '${meter.used} of ${meter.cap} used';
    }

    return SettingsRow(
      icon: _icons[metric] ?? Icons.data_usage_rounded,
      gradient: Aurora.gradBlue,
      title: _labels[metric] ?? metric,
      subtitle: subtitle,
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
