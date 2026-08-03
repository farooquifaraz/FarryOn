import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme.dart';
import '../../state/providers.dart';

/// True while a connect flow is running — a second tap (dashboard pill AND
/// Settings card share this) must not start a second scan.
bool _flowActive = false;

/// Shared "connect glasses" flow for the dashboard pill and the Settings
/// card: clean-slate scan → nothing found = honest snackbar · one pair =
/// connect it · several pairs = HeyCyan-style chooser sheet, and the pick
/// becomes the persisted auto-connect target.
///
/// Returns true when a connect was actually issued — false means the user
/// cancelled the sheet or nothing was found, so callers (the pill) should
/// drop their "Connecting…" state right away instead of timing it out.
Future<bool> runGlassesConnectFlow(BuildContext context, WidgetRef ref) async {
  if (_flowActive) return false;
  _flowActive = true;
  try {
    return await _runFlow(context, ref);
  } finally {
    _flowActive = false;
  }
}

Future<bool> _runFlow(BuildContext context, WidgetRef ref) async {
  final notifier = ref.read(liveProvider.notifier);
  final hits = await notifier.scanGlassesForPicker();
  if (!context.mounted) return false;
  if (hits.isEmpty) {
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('No glasses found — make sure they are on and nearby'),
      ),
    );
    return false;
  }
  String? mac;
  if (hits.length == 1) {
    mac = hits.first.mac;
  } else {
    mac = await showModalBottomSheet<String>(
      context: context,
      backgroundColor: Aurora.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Padding(
              padding: EdgeInsets.fromLTRB(20, 18, 20, 6),
              child: Text(
                'Choose glasses',
                style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700),
              ),
            ),
            for (final h in hits)
              ListTile(
                leading: Icon(
                  h.connected
                      ? Icons.bluetooth_connected
                      : Icons.bluetooth_searching,
                  color: h.connected ? Aurora.teal : Aurora.textMuted,
                ),
                title: Text(h.name),
                subtitle: Text(
                  h.connected
                      ? 'Paired with this phone · ${h.mac}'
                      : h.rssi != 0
                          ? 'Nearby (${h.rssi} dBm) · ${h.mac}'
                          // Bonded fold-in only — nothing heard it live, so
                          // it is probably powered off; picking it means a
                          // ~60 s timeout. Warn instead of hiding it (it may
                          // be classic-connected without advertising).
                          : 'Not seen nearby — may be off · ${h.mac}',
                  style: const TextStyle(fontSize: 12),
                ),
                onTap: () => Navigator.pop(ctx, h.mac),
              ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }
  if (mac == null) return false; // sheet dismissed — stay disconnected
  await notifier.connectGlassesTo(mac);
  return true;
}
