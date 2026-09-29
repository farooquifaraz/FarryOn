import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/config.dart';
import '../../core/theme.dart';
import '../../core/ui.dart';
import '../../state/live_state.dart';
import '../../state/providers.dart';

/// Settings → Glasses: the connected pair, and what can be done to it.
///
/// Modelled on the vendor app's "My glasses" screen (2026-09-29): the pair
/// on a card, then Restart, Restore factory settings and About. Restart and
/// reset are the vendor's own control commands (see the native bridge);
/// both ask first, in the middle of the screen, and both are disabled the
/// moment the link is down — a command to glasses that are not there is a
/// promise the app cannot keep.
class GlassesPage extends ConsumerWidget {
  const GlassesPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final live = ref.watch(liveProvider);
    final connected = live.glassesConnected;
    final firmware = live.glassesInfo['btFirmware']?.toString();

    return Scaffold(
      backgroundColor: Aurora.base,
      appBar: AppBar(title: const Text('My glasses')),
      body: ListView(
        padding: EdgeInsets.fromLTRB(
            16, 8, 16, 28 + MediaQuery.paddingOf(context).bottom),
        children: [
          _PairCard(live: live, firmware: firmware),
          const SizedBox(height: 20),
          SettingsGroup(children: [
            SettingsRow(
              icon: Icons.restart_alt_rounded,
              gradient: Aurora.gradTeal,
              title: 'Restart',
              subtitle: connected
                  ? 'They come back on their own in about 20 seconds'
                  : 'Connect the glasses first',
              onTap: connected ? () => _confirmRestart(context, ref) : null,
            ),
            SettingsRow(
              icon: Icons.delete_forever_rounded,
              gradient: Aurora.gradCoral,
              title: 'Restore factory settings',
              subtitle: connected
                  ? 'Erases everything on the glasses'
                  : 'Connect the glasses first',
              onTap: connected ? () => _confirmReset(context, ref) : null,
            ),
            SettingsRow(
              icon: Icons.info_outline_rounded,
              gradient: Aurora.gradPurple,
              title: 'About',
              subtitle: firmware ?? 'Versions and addresses',
              onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(
                builder: (_) => const GlassesAboutPage(),
              )),
              showDivider: false,
            ),
          ]),
          const SizedBox(height: 24),
          if (connected)
            OutlinedButton(
              onPressed: () => _confirmDisconnect(context, ref),
              style: OutlinedButton.styleFrom(
                foregroundColor: Aurora.textPrimary,
                side: const BorderSide(color: Aurora.glassBorder),
                minimumSize: const Size.fromHeight(50),
                shape: const StadiumBorder(),
              ),
              child: const Text('Disconnect'),
            ),
        ],
      ),
    );
  }

  Future<void> _confirmDisconnect(BuildContext context, WidgetRef ref) async {
    final sure = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Disconnect glasses?'),
        content:
            const Text('They will stay disconnected until you connect again.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Disconnect'),
          ),
        ],
      ),
    );
    if (sure == true) await ref.read(liveProvider.notifier).disconnectGlasses();
  }

  Future<void> _confirmRestart(BuildContext context, WidgetRef ref) async {
    final sure = await showDialog<bool>(
      context: context,
      builder: (ctx) => const GlassesConfirmDialog(
        icon: Icons.restart_alt_rounded,
        iconColor: Aurora.mint,
        title: 'Restart the glasses?',
        body: 'They switch off and come back on their own in about 20 '
            'seconds, then reconnect to this phone. Your session continues; '
            'the phone mic listens meanwhile.',
        confirmLabel: 'Restart',
        confirmColor: Aurora.teal,
        confirmTextColor: Aurora.tealInk,
      ),
    );
    if (sure != true || !context.mounted) return;
    final ok = await ref.read(liveProvider.notifier).restartGlasses();
    if (!context.mounted) return;
    ScaffoldMessenger.of(context)
      ..clearSnackBars()
      ..showSnackBar(SnackBar(
        content: Text(ok
            ? 'Restarting — the glasses reconnect in about 20 seconds.'
            : 'Could not reach the glasses. Are they connected?'),
      ));
  }

  Future<void> _confirmReset(BuildContext context, WidgetRef ref) async {
    final pending = ref.read(liveProvider).pendingMedia;
    final unsynced = pending == null
        ? null
        : [
            if (pending.photos > 0)
              '${pending.photos} photo${pending.photos == 1 ? '' : 's'}',
            if (pending.videos > 0)
              '${pending.videos} video${pending.videos == 1 ? '' : 's'}',
          ].join(', ');
    final sure = await showDialog<bool>(
      context: context,
      builder: (ctx) => GlassesConfirmDialog(
        icon: Icons.warning_amber_rounded,
        iconColor: Aurora.danger,
        title: 'Restore factory settings?',
        body: 'This erases everything on the glasses: the pairing with this '
            'phone, Wi‑Fi settings and any photos or videos still on them. '
            'Sync your media first. You will pair the glasses again '
            'afterwards.',
        notice: unsynced == null || unsynced.isEmpty
            ? null
            : '$unsynced on the glasses are not synced yet',
        confirmLabel: 'Erase & reset',
        confirmColor: Aurora.danger,
        confirmTextColor: Colors.white,
      ),
    );
    if (sure != true || !context.mounted) return;
    final ok = await ref.read(liveProvider.notifier).factoryResetGlasses();
    if (!context.mounted) return;
    ScaffoldMessenger.of(context)
      ..clearSnackBars()
      ..showSnackBar(SnackBar(
        content: Text(ok
            ? 'Reset sent. Pair the glasses again from Settings → Glasses.'
            : 'Could not reach the glasses. Are they connected?'),
      ));
    if (ok) Navigator.of(context).pop();
  }
}

/// The pair on a card: name, address, firmware, battery and link state.
class _PairCard extends StatelessWidget {
  const _PairCard({required this.live, required this.firmware});
  final LiveSessionState live;
  final String? firmware;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final connected = live.glassesConnected;
    final detail = [
      if (live.glassesMac != null) live.glassesMac!,
      if (firmware != null) 'firmware $firmware',
    ].join(' · ');
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Aurora.glass,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: Aurora.glassBorder),
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(live.glassesName ?? 'No glasses yet',
                    style: theme.textTheme.titleMedium
                        ?.copyWith(fontWeight: FontWeight.w700)),
                if (detail.isNotEmpty) ...[
                  const SizedBox(height: 4),
                  Text(detail,
                      style: theme.textTheme.bodySmall
                          ?.copyWith(color: Aurora.textMuted)),
                ],
                const SizedBox(height: 6),
                Row(children: [
                  Container(
                    width: 8,
                    height: 8,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: connected ? Aurora.mint : Aurora.textMuted,
                    ),
                  ),
                  const SizedBox(width: 8),
                  Text(
                    connected
                        ? (live.glassesBattery != null
                            ? 'Connected · ${live.glassesBattery}%'
                            : 'Connected')
                        : 'Disconnected',
                    style: theme.textTheme.bodyMedium?.copyWith(
                      color: connected ? Aurora.mint : Aurora.textMuted,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ]),
              ],
            ),
          ),
          const Icon(Icons.visibility_rounded, size: 44, color: Aurora.textPrimary),
        ],
      ),
    );
  }
}

/// A centred confirmation: icon, title, body, optional amber notice, then the
/// action over Cancel. Returns true on the action.
class GlassesConfirmDialog extends StatelessWidget {
  const GlassesConfirmDialog({
    super.key,
    required this.icon,
    required this.iconColor,
    required this.title,
    required this.body,
    required this.confirmLabel,
    required this.confirmColor,
    required this.confirmTextColor,
    this.notice,
  });

  final IconData icon;
  final Color iconColor;
  final String title;
  final String body;
  final String? notice;
  final String confirmLabel;
  final Color confirmColor;
  final Color confirmTextColor;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Dialog(
      backgroundColor: Aurora.surface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(22),
        side: const BorderSide(color: Aurora.glassBorder),
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(22, 24, 22, 22),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 48,
              height: 48,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: iconColor.withValues(alpha: 0.15),
              ),
              child: Icon(icon, color: iconColor),
            ),
            const SizedBox(height: 12),
            Text(title,
                textAlign: TextAlign.center,
                style: theme.textTheme.titleMedium
                    ?.copyWith(fontWeight: FontWeight.w700)),
            const SizedBox(height: 10),
            Text(body,
                textAlign: TextAlign.center,
                style: theme.textTheme.bodyMedium
                    ?.copyWith(color: Aurora.textMuted, height: 1.45)),
            if (notice != null) ...[
              const SizedBox(height: 12),
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                decoration: BoxDecoration(
                  color: Aurora.amber.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Row(children: [
                  const Icon(Icons.photo_library_outlined,
                      size: 16, color: Aurora.amber),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(notice!,
                        style: theme.textTheme.bodySmall?.copyWith(
                            color: Aurora.amber, fontWeight: FontWeight.w600)),
                  ),
                ]),
              ),
            ],
            const SizedBox(height: 18),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              style: FilledButton.styleFrom(
                backgroundColor: confirmColor,
                foregroundColor: confirmTextColor,
                minimumSize: const Size.fromHeight(48),
                shape: const StadiumBorder(),
              ),
              child: Text(confirmLabel),
            ),
            const SizedBox(height: 10),
            OutlinedButton(
              onPressed: () => Navigator.pop(context, false),
              style: OutlinedButton.styleFrom(
                foregroundColor: Aurora.textPrimary,
                side: const BorderSide(color: Aurora.glassBorder),
                minimumSize: const Size.fromHeight(48),
                shape: const StadiumBorder(),
              ),
              child: const Text('Cancel'),
            ),
          ],
        ),
      ),
    );
  }
}

/// Settings → Glasses → About: what the glasses report about themselves.
/// Asks the glasses on open; a pair that is not connected shows what they
/// last said, if anything.
class GlassesAboutPage extends ConsumerStatefulWidget {
  const GlassesAboutPage({super.key});

  @override
  ConsumerState<GlassesAboutPage> createState() => _GlassesAboutPageState();
}

class _GlassesAboutPageState extends ConsumerState<GlassesAboutPage> {
  bool _asking = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _refresh());
  }

  Future<void> _refresh() async {
    if (!mounted) return;
    setState(() => _asking = true);
    await ref.read(liveProvider.notifier).refreshGlassesInfo();
    if (mounted) setState(() => _asking = false);
  }

  @override
  Widget build(BuildContext context) {
    final live = ref.watch(liveProvider);
    final info = live.glassesInfo;
    String v(String key) => info[key]?.toString() ?? '—';
    final battery = live.glassesBattery;

    return Scaffold(
      backgroundColor: Aurora.base,
      appBar: AppBar(title: const Text('About')),
      body: ListView(
        padding: EdgeInsets.fromLTRB(
            16, 8, 16, 28 + MediaQuery.paddingOf(context).bottom),
        children: [
          SettingsGroup(children: [
            _InfoRow('Bluetooth name', live.glassesName ?? '—'),
            _InfoRow('MAC address', live.glassesMac ?? '—'),
            _InfoRow(
              'Battery',
              battery == null
                  ? '—'
                  : '$battery%${battery >= 100 ? ' · charged' : ''}',
              valueColor: battery == null ? null : Aurora.mint,
              last: true,
            ),
          ]),
          const SizedBox(height: 20),
          const SectionLabel('Glasses'),
          SettingsGroup(children: [
            _InfoRow('Hardware version', v('btHardware')),
            _InfoRow('Software version', v('btFirmware')),
            _InfoRow('Wi‑Fi hardware version', v('wifiHardware')),
            _InfoRow('Wi‑Fi software version', v('wifiFirmware'), last: true),
          ]),
          const SizedBox(height: 20),
          const SectionLabel('App'),
          SettingsGroup(children: [
            _InfoRow('FarryOn version',
                AppConfig.installedVersion.isEmpty
                    ? '1.0.0'
                    : AppConfig.installedVersion,
                last: true),
          ]),
          const SizedBox(height: 24),
          OutlinedButton.icon(
            onPressed: _asking || !live.glassesConnected ? null : _refresh,
            style: OutlinedButton.styleFrom(
              foregroundColor: Aurora.textPrimary,
              side: const BorderSide(color: Aurora.glassBorder),
              minimumSize: const Size.fromHeight(44),
              shape: const StadiumBorder(),
            ),
            icon: const Icon(Icons.refresh_rounded, size: 18),
            label: Text(live.glassesConnected
                ? 'Refresh from glasses'
                : 'Connect the glasses to refresh'),
          ),
        ],
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow(this.label, this.value, {this.valueColor, this.last = false});
  final String label;
  final String value;
  final Color? valueColor;
  final bool last;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      decoration: last
          ? null
          : const BoxDecoration(
              border: Border(bottom: BorderSide(color: Aurora.glassBorder))),
      child: Row(children: [
        Expanded(
          child: Text(label,
              style: theme.textTheme.bodyMedium
                  ?.copyWith(fontWeight: FontWeight.w600)),
        ),
        Text(value,
            style: theme.textTheme.bodySmall
                ?.copyWith(color: valueColor ?? Aurora.textMuted)),
      ]),
    );
  }
}
