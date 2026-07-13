import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme.dart';
import '../../core/ui.dart';
import '../../data/data_api.dart';
import '../../state/providers.dart';
import 'data_common.dart';

/// The saved notes the assistant remembered ("remember …"), read over REST from
/// the same backend the live session uses. Delete supported; add is voice-only.
class NotesScreen extends ConsumerStatefulWidget {
  const NotesScreen({super.key});

  static Future<void> open(BuildContext context) => Navigator.of(context).push(
        MaterialPageRoute<void>(builder: (_) => const NotesScreen()),
      );

  @override
  ConsumerState<NotesScreen> createState() => _NotesScreenState();
}

class _NotesScreenState extends ConsumerState<NotesScreen> {
  late final DataApi _api = ref.read(dataApiProvider);
  late Future<List<NoteItem>> _future = _api.notes();

  void _reload() => setState(() => _future = _api.notes());

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Aurora.base,
      appBar: AppBar(
        title: const Row(
          children: [
            GradientIcon(Icons.sticky_note_2_rounded,
                gradient: Aurora.gradGreen, size: 22),
            SizedBox(width: 10),
            Text('Notes'),
          ],
        ),
      ),
      body: RefreshIndicator(
        onRefresh: () async => _reload(),
        child: FutureBuilder<List<NoteItem>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snap.hasError) {
              return _scrollable(DataErrorState(onRetry: _reload));
            }
            final notes = snap.data ?? const [];
            if (notes.isEmpty) {
              return _scrollable(const DataEmptyState(
                icon: Icons.sticky_note_2_rounded,
                gradient: Aurora.gradGreen,
                label: 'No notes yet.\nSay "remember…" to add one.',
              ));
            }
            return ListView.separated(
              padding: const EdgeInsets.all(14),
              itemCount: notes.length,
              separatorBuilder: (_, __) => const SizedBox(height: 12),
              itemBuilder: (context, i) {
                final n = notes[i];
                final created = dataDate(n.createdAt);
                return Container(
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    color: Aurora.surfaceHigh,
                    borderRadius: BorderRadius.circular(16),
                    border: Border.all(color: Aurora.glassBorder),
                  ),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const GradientIconTile(Icons.sticky_note_2_rounded,
                          gradient: Aurora.gradGreen,
                          tileSize: 38,
                          iconSize: 20),
                      const SizedBox(width: 13),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(n.text,
                                style: const TextStyle(
                                    color: Aurora.textPrimary,
                                    fontSize: 15,
                                    height: 1.45)),
                            if (created != null) ...[
                              const SizedBox(height: 9),
                              Row(
                                children: [
                                  const Icon(Icons.schedule_rounded,
                                      size: 13, color: Aurora.textMuted),
                                  const SizedBox(width: 4),
                                  Text(created,
                                      style: const TextStyle(
                                          color: Aurora.textMuted,
                                          fontSize: 11)),
                                ],
                              ),
                            ],
                          ],
                        ),
                      ),
                      const SizedBox(width: 4),
                      InkWell(
                        borderRadius: BorderRadius.circular(20),
                        onTap: () async {
                          await _api.deleteNote(n.id);
                          _reload();
                        },
                        child: const Padding(
                          padding: EdgeInsets.all(6),
                          child: Icon(Icons.delete_outline_rounded,
                              size: 20, color: Aurora.textMuted),
                        ),
                      ),
                    ],
                  ),
                );
              },
            );
          },
        ),
      ),
    );
  }

  // Empty / error states must still be pull-to-refreshable, so give them a
  // scroll parent that always overscrolls.
  Widget _scrollable(Widget child) => ListView(
        padding: const EdgeInsets.only(top: 120),
        children: [child],
      );
}
