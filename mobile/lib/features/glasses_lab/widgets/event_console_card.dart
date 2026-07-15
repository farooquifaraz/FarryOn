import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../../core/theme.dart';
import '../glasses_lab_controller.dart';
import 'lab_card.dart';

/// Card 6 — timestamped console of every event the glasses emit (including
/// raw/unmapped ones), with copy-all. This is where gestures, wear detection
/// and undocumented firmware notifications get discovered and written down
/// in LAB_NOTES.md.
class EventConsoleCard extends StatelessWidget {
  const EventConsoleCard(this.c, {super.key});

  final GlassesLabController c;

  Color _color(String type) => switch (type) {
        'error' => Aurora.danger,
        'gesture' || 'wearState' => Aurora.purpleSoft,
        'thumbnail' || 'battery' => Aurora.mint,
        _ => Aurora.textMuted,
      };

  @override
  Widget build(BuildContext context) {
    return LabCard(
      icon: Icons.terminal,
      title: 'Event console',
      status: '${c.events.length}',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              TextButton.icon(
                onPressed: c.events.isEmpty
                    ? null
                    : () async {
                        await Clipboard.setData(
                            ClipboardData(text: c.exportEvents()));
                        if (context.mounted) {
                          ScaffoldMessenger.of(context).showSnackBar(
                            const SnackBar(
                                content: Text('Events copied to clipboard')),
                          );
                        }
                      },
                icon: const Icon(Icons.copy_all, size: 16),
                label: const Text('Copy all'),
              ),
              TextButton.icon(
                onPressed: c.events.isEmpty ? null : c.clearEvents,
                icon: const Icon(Icons.delete_outline, size: 16),
                label: const Text('Clear'),
              ),
            ],
          ),
          Container(
            height: 180,
            width: double.infinity,
            padding: const EdgeInsets.all(8),
            decoration: BoxDecoration(
              color: Aurora.base,
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: Aurora.glassBorder),
            ),
            child: c.events.isEmpty
                ? const Center(
                    child: Text(
                      'Device events will appear here.',
                      style:
                          TextStyle(color: Aurora.textMuted, fontSize: 12),
                    ),
                  )
                : ListView.builder(
                    reverse: true,
                    itemCount: c.events.length,
                    itemBuilder: (context, i) {
                      final e = c.events[c.events.length - 1 - i];
                      return Text(
                        e.format(),
                        style: TextStyle(
                          fontFamily: 'monospace',
                          fontSize: 10.5,
                          color: _color(e.type),
                        ),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }
}
