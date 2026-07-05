import 'package:flutter/material.dart';

import '../../../core/theme.dart';
import '../glasses_lab_controller.dart';
import 'lab_card.dart';

/// Card 1 — BLE connection: scan, pick a device, connect/disconnect,
/// auto-reconnect toggle. Everything else in the Lab needs this first.
class ConnectionCard extends StatelessWidget {
  const ConnectionCard(this.c, {super.key});

  final GlassesLabController c;

  (String, Color) get _status => switch (c.connectionState) {
        'connected' => ('connected', Aurora.teal),
        'connecting' => ('connecting…', Aurora.amber),
        _ => ('disconnected', Aurora.textMuted),
      };

  @override
  Widget build(BuildContext context) {
    final (label, color) = _status;
    return LabCard(
      icon: Icons.bluetooth_searching,
      title: 'Connection',
      status: label,
      statusColor: color,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              FilledButton.icon(
                onPressed: c.scanning ? null : c.startScan,
                icon: c.scanning
                    ? const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.radar, size: 18),
                label: Text(c.scanning ? 'Scanning…' : 'Scan'),
              ),
              const SizedBox(width: 8),
              if (c.connectionState != 'disconnected')
                OutlinedButton.icon(
                  onPressed: c.disconnect,
                  icon: const Icon(Icons.link_off, size: 18),
                  label: const Text('Disconnect'),
                ),
            ],
          ),
          if (c.devices.isNotEmpty) ...[
            const SizedBox(height: 10),
            for (final d in c.devices)
              ListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                leading:
                    const Icon(Icons.visibility, color: Aurora.purpleSoft),
                title: Text(d.name,
                    style: const TextStyle(color: Aurora.textPrimary)),
                subtitle: Text('${d.mac}  ·  ${d.rssi} dBm',
                    style: const TextStyle(
                        color: Aurora.textMuted, fontSize: 11.5)),
                trailing: c.connectedMac == d.mac
                    ? const LabStatusPill('this device', color: Aurora.teal)
                    : TextButton(
                        onPressed: () => c.connect(d.mac),
                        child: const Text('Connect'),
                      ),
              ),
          ] else if (!c.scanning) ...[
            const SizedBox(height: 6),
            const Text(
              'No devices yet — power on the L801 and tap Scan.',
              style: TextStyle(color: Aurora.textMuted, fontSize: 12.5),
            ),
          ],
          const SizedBox(height: 4),
          SwitchListTile(
            dense: true,
            contentPadding: EdgeInsets.zero,
            title: const Text('Auto-reconnect',
                style: TextStyle(fontSize: 13.5, color: Aurora.textPrimary)),
            subtitle: const Text(
              'Re-attach automatically when the glasses come back in range',
              style: TextStyle(color: Aurora.textMuted, fontSize: 11.5),
            ),
            value: c.autoReconnect,
            onChanged: c.toggleAutoReconnect,
          ),
        ],
      ),
    );
  }
}
