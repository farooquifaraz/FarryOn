import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme.dart';
import '../../state/providers.dart';

/// Shared "connect glasses" flow for the dashboard pill and the Settings
/// card: clean-slate scan → nothing found = honest snackbar · one pair =
/// connect it · several pairs = HeyCyan-style chooser sheet, and the pick
/// becomes the persisted auto-connect target.
Future<void> runGlassesConnectFlow(BuildContext context, WidgetRef ref) async {
  final notifier = ref.read(liveProvider.notifier);
  final hits = await notifier.scanGlassesForPicker();
  if (!context.mounted) return;
  if (hits.isEmpty) {
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('No glasses found — make sure they are on and nearby'),
      ),
    );
    return;
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
                          : h.mac,
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
  if (mac == null) return; // sheet dismissed — stay disconnected
  await notifier.connectGlassesTo(mac);
}
