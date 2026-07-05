import 'package:flutter/material.dart';

import '../../../core/theme.dart';
import '../glasses_lab_controller.dart';
import 'lab_card.dart';

/// Card 3 — the Photo-Trigger Vision rehearsal: plain photo (stays on the
/// glasses) and AI photo (capture + BLE thumbnail back to the app). Shows the
/// measured capture→thumbnail latency, which is THE number Stage B's vision
/// pipeline depends on (target ≤ 3000 ms).
class CameraCard extends StatelessWidget {
  const CameraCard(this.c, {super.key});

  final GlassesLabController c;

  @override
  Widget build(BuildContext context) {
    final connected = c.connectionState == 'connected';
    final latest = c.thumbnails.isEmpty ? null : c.thumbnails.first;
    return LabCard(
      icon: Icons.photo_camera_outlined,
      title: 'Camera / Photo-Trigger',
      status: c.photoInFlight ? 'capturing…' : null,
      statusColor: Aurora.amber,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              FilledButton.icon(
                onPressed:
                    connected && !c.photoInFlight ? c.takeAiPhoto : null,
                icon: const Icon(Icons.center_focus_strong, size: 18),
                label: const Text('AI photo + thumbnail'),
              ),
              OutlinedButton.icon(
                onPressed: connected ? c.takePhoto : null,
                icon: const Icon(Icons.photo_camera, size: 18),
                label: const Text('Photo only'),
              ),
            ],
          ),
          const SizedBox(height: 12),
          if (latest == null)
            const Text(
              'No thumbnails yet. "AI photo" must return a JPEG here in '
              '≤ 3 s for the Stage B vision pipeline to be viable.',
              style: TextStyle(color: Aurora.textMuted, fontSize: 12.5),
            )
          else ...[
            ClipRRect(
              borderRadius: BorderRadius.circular(12),
              child: Image.memory(
                latest.jpeg,
                height: 160,
                width: double.infinity,
                fit: BoxFit.cover,
                gaplessPlayback: true,
              ),
            ),
            const SizedBox(height: 6),
            Row(
              children: [
                LabStatusPill(
                  '${latest.elapsedMs} ms',
                  color: latest.elapsedMs <= 3000 ? Aurora.teal : Aurora.danger,
                ),
                const SizedBox(width: 8),
                Text(
                  '${latest.jpeg.length ~/ 1024} KB  ·  '
                  '${latest.at.toIso8601String().substring(11, 19)}',
                  style: const TextStyle(
                      color: Aurora.textMuted, fontSize: 11.5),
                ),
              ],
            ),
            if (c.thumbnails.length > 1) ...[
              const SizedBox(height: 8),
              SizedBox(
                height: 48,
                child: ListView.separated(
                  scrollDirection: Axis.horizontal,
                  itemCount: c.thumbnails.length - 1,
                  separatorBuilder: (_, __) => const SizedBox(width: 6),
                  itemBuilder: (context, i) => ClipRRect(
                    borderRadius: BorderRadius.circular(6),
                    child: Image.memory(
                      c.thumbnails[i + 1].jpeg,
                      width: 64,
                      fit: BoxFit.cover,
                    ),
                  ),
                ),
              ),
            ],
          ],
        ],
      ),
    );
  }
}
