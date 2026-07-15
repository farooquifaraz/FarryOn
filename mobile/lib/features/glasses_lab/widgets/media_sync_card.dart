import 'package:flutter/material.dart';

import '../../../core/theme.dart';
import '../glasses_lab_controller.dart';
import 'lab_card.dart';

/// Card 5 — WiFi-P2P media sync: pulls full-resolution photos/videos off the
/// glasses' storage (the high-quality path for OCR/receipt use-cases).
class MediaSyncCard extends StatelessWidget {
  const MediaSyncCard(this.c, {super.key});

  final GlassesLabController c;

  @override
  Widget build(BuildContext context) {
    final connected = c.connectionState == 'connected';
    return LabCard(
      icon: Icons.sync,
      title: 'Media sync (WiFi)',
      status: c.syncing ? 'syncing ${c.syncPct}%' : null,
      statusColor: Aurora.amber,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              FilledButton.icon(
                onPressed: connected && !c.syncing ? c.startWifiSync : null,
                icon: const Icon(Icons.download, size: 18),
                label: const Text('Start sync'),
              ),
              if (c.syncing)
                OutlinedButton.icon(
                  onPressed: c.stopWifiSync,
                  icon: const Icon(Icons.stop, size: 18),
                  label: const Text('Stop'),
                ),
            ],
          ),
          const SizedBox(height: 10),
          if (c.mediaTotal != null) ...[
            LabKv(
              'Glasses memory',
              '📷 ${c.mediaImg}  ·  🎥 ${c.mediaVid}  ·  🎙 ${c.mediaRec}'
              '  ·  total ${c.mediaTotal}'
              '${c.mediaTotal == 0 ? '  (empty ✓)' : ''}',
            ),
            const SizedBox(height: 6),
          ],
          if (c.syncing || c.syncPct > 0) ...[
            LinearProgressIndicator(
              value: c.syncPct / 100,
              minHeight: 6,
              borderRadius: BorderRadius.circular(3),
            ),
            const SizedBox(height: 6),
            LabKv(
              c.syncFile ?? '—',
              '${c.syncPct}%'
              '${c.syncSpeedKbps > 0 ? '  ·  ${c.syncSpeedKbps.toStringAsFixed(0)} kB/s' : ''}',
            ),
          ] else
            const Text(
              'Pulls full-resolution media over WiFi-P2P. Measure transfer '
              'speed here — it decides whether receipts/documents can use '
              'the full-res path in Stage B.',
              style: TextStyle(color: Aurora.textMuted, fontSize: 12.5),
            ),
        ],
      ),
    );
  }
}
