import 'package:flutter/material.dart';

import '../../../core/theme.dart';
import '../glasses_lab_controller.dart';
import 'lab_card.dart';

/// Card 2 — live device state: battery, charging, wear detection, firmware
/// versions. Verifies the event pipeline (0x05 battery reports etc.) works.
class DeviceInfoCard extends StatelessWidget {
  const DeviceInfoCard(this.c, {super.key});

  final GlassesLabController c;

  IconData get _batteryIcon {
    final pct = c.batteryPct;
    if (c.charging) return Icons.battery_charging_full;
    if (pct == null) return Icons.battery_unknown;
    if (pct >= 80) return Icons.battery_full;
    if (pct >= 50) return Icons.battery_5_bar;
    if (pct >= 20) return Icons.battery_3_bar;
    return Icons.battery_alert;
  }

  @override
  Widget build(BuildContext context) {
    final connected = c.connectionState == 'connected';
    return LabCard(
      icon: Icons.info_outline,
      title: 'Device info',
      status: c.worn == null ? null : (c.worn! ? 'worn' : 'not worn'),
      statusColor: c.worn == true ? Aurora.teal : Aurora.textMuted,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(_batteryIcon,
                  size: 22,
                  color: (c.batteryPct ?? 100) < 20
                      ? Aurora.danger
                      : Aurora.mint),
              const SizedBox(width: 6),
              Text(
                c.batteryPct == null
                    ? 'Battery —'
                    : 'Battery ${c.batteryPct}%${c.charging ? ' (charging)' : ''}',
                style: const TextStyle(
                    fontSize: 13.5, color: Aurora.textPrimary),
              ),
              const Spacer(),
              TextButton.icon(
                onPressed: connected ? c.refreshDeviceInfo : null,
                icon: const Icon(Icons.refresh, size: 16),
                label: const Text('Refresh'),
              ),
            ],
          ),
          const SizedBox(height: 6),
          if (c.deviceInfo.isEmpty)
            const Text(
              'Connect and tap Refresh to read firmware/hardware versions.',
              style: TextStyle(color: Aurora.textMuted, fontSize: 12.5),
            )
          else ...[
            for (final e in c.deviceInfo.entries)
              if (e.key != 'type') LabKv(e.key, '${e.value}'),
          ],
        ],
      ),
    );
  }
}
