import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';

import '../../core/log_store.dart';

/// A live view of the in-app debug log with one-tap **Share** and **Copy**, so
/// the user can report a problem with the full tool/usage/error trail (and the
/// AI provider that was in use) instead of taking screenshots.
class DebugLogsScreen extends StatelessWidget {
  const DebugLogsScreen({super.key});

  static Future<void> open(BuildContext context) => Navigator.of(context).push(
        MaterialPageRoute<void>(builder: (_) => const DebugLogsScreen()),
      );

  /// Write the log to a temp .txt file and share it as a FILE attachment — a
  /// full session log is far too long to paste into a chat box, but as a file
  /// it sends to WhatsApp/email in one tap.
  Future<void> _shareAsFile(BuildContext context) async {
    try {
      final dir = await getTemporaryDirectory();
      final stamp = DateTime.now()
          .toIso8601String()
          .replaceAll(RegExp(r'[:.]'), '-');
      final file = File('${dir.path}/farryon_log_$stamp.txt');
      await file.writeAsString(LogStore.instance.export());
      await Share.shareXFiles(
        [XFile(file.path, mimeType: 'text/plain')],
        subject: 'FarryOn debug log',
      );
    } catch (e) {
      // Fallback to plain-text share if writing the file fails.
      await Share.share(LogStore.instance.export(), subject: 'FarryOn debug log');
    }
  }

  Color _levelColor(String level, ColorScheme cs) => switch (level) {
        'ERROR' => cs.error,
        'WARN' => Colors.orange,
        'DEBUG' => cs.onSurfaceVariant,
        _ => cs.onSurface,
      };

  @override
  Widget build(BuildContext context) {
    final store = LogStore.instance;
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Debug logs'),
        actions: [
          IconButton(
            tooltip: 'Copy all',
            icon: const Icon(Icons.copy_all),
            onPressed: () async {
              await Clipboard.setData(ClipboardData(text: store.export()));
              if (context.mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('Logs copied to clipboard')),
                );
              }
            },
          ),
          IconButton(
            tooltip: 'Share as file',
            icon: const Icon(Icons.ios_share),
            onPressed: () => _shareAsFile(context),
          ),
          IconButton(
            tooltip: 'Clear',
            icon: const Icon(Icons.delete_outline),
            onPressed: store.clear,
          ),
        ],
      ),
      body: ValueListenableBuilder<int>(
        valueListenable: store.revision,
        builder: (context, _, __) {
          final entries = store.entries;
          if (entries.isEmpty) {
            return const Center(child: Text('No logs yet — use the app a bit.'));
          }
          return ListView.builder(
            reverse: true, // newest at the bottom, but scrolled into view
            padding: const EdgeInsets.all(12),
            itemCount: entries.length,
            itemBuilder: (context, i) {
              final e = entries[entries.length - 1 - i];
              return Padding(
                padding: const EdgeInsets.symmetric(vertical: 2),
                child: SelectableText(
                  e.format(),
                  style: TextStyle(
                    fontFamily: 'monospace',
                    fontSize: 11.5,
                    color: _levelColor(e.level, cs),
                  ),
                ),
              );
            },
          );
        },
      ),
    );
  }
}
