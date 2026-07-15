import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme.dart';
import '../../core/ui.dart';
import '../../data/data_api.dart';
import '../../state/providers.dart';
import 'data_common.dart';

/// The saved notes the assistant remembered ("remember …"), read over REST from
/// the same backend the live session uses. Swipe a note to delete it (with a
/// confirm + optimistic removal); adding is voice-only.
class NotesScreen extends ConsumerStatefulWidget {
  const NotesScreen({super.key});

  static Future<void> open(BuildContext context) => Navigator.of(context).push(
        MaterialPageRoute<void>(builder: (_) => const NotesScreen()),
      );

  @override
  ConsumerState<NotesScreen> createState() => _NotesScreenState();
}

class _NotesScreenState extends ConsumerState<NotesScreen> {
  static const Color _accent = Color(0xFF97C459); // green ramp mid

  late final DataApi _api = ref.read(dataApiProvider);
  List<NoteItem>? _notes;
  bool _loading = true;
  bool _error = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = _notes == null;
      _error = false;
    });
    try {
      final n = await _api.notes();
      if (!mounted) return;
      setState(() {
        _notes = n;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = true;
        _loading = false;
      });
    }
  }

  /// Optimistic delete: drop the row immediately, roll back + warn on failure.
  Future<void> _delete(NoteItem n) async {
    final prev = _notes;
    setState(() => _notes = _notes?.where((x) => x.id != n.id).toList());
    try {
      await _api.deleteNote(n.id);
    } catch (_) {
      if (!mounted) return;
      setState(() => _notes = prev);
      dataSnack(context, "Couldn't delete the note — try again.", error: true);
    }
  }

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
        onRefresh: _load,
        child: _body(),
      ),
    );
  }

  Widget _body() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error) {
      return _scrollable(DataErrorState(onRetry: _load));
    }
    final notes = _notes ?? const [];
    if (notes.isEmpty) {
      return _scrollable(const DataEmptyState(
        icon: Icons.sticky_note_2_rounded,
        gradient: Aurora.gradGreen,
        label: 'No notes yet.\nSay "remember…" to add one.',
      ));
    }
    return ListView.separated(
      padding: const EdgeInsets.fromLTRB(14, 14, 14, 28),
      itemCount: notes.length + 1,
      separatorBuilder: (_, i) => SizedBox(height: i == 0 ? 0 : 12),
      itemBuilder: (context, i) {
        if (i == 0) {
          return DataCountHeader(
            text: '${notes.length} ${notes.length == 1 ? "note" : "notes"}',
            accent: _accent,
          );
        }
        return _noteCard(notes[i - 1]);
      },
    );
  }

  Widget _noteCard(NoteItem n) {
    final created = dataDate(n.createdAt);
    return SwipeToDelete(
      dismissKey: ValueKey('note-${n.id}'),
      confirm: () => confirmAction(
        context,
        title: 'Delete note?',
        message: n.text,
      ),
      onDismissed: () => _delete(n),
      child: Container(
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
                gradient: Aurora.gradGreen, tileSize: 38, iconSize: 20),
            const SizedBox(width: 13),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SelectableText(
                    n.text,
                    style: const TextStyle(
                        color: Aurora.textPrimary, fontSize: 15, height: 1.45),
                  ),
                  if (created != null) ...[
                    const SizedBox(height: 9),
                    Row(
                      children: [
                        const Icon(Icons.schedule_rounded,
                            size: 13, color: Aurora.textMuted),
                        const SizedBox(width: 4),
                        Text(created,
                            style: const TextStyle(
                                color: Aurora.textMuted, fontSize: 11)),
                      ],
                    ),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  // Empty / error states must still be pull-to-refreshable.
  Widget _scrollable(Widget child) => ListView(
        padding: const EdgeInsets.only(top: 120),
        children: [child],
      );
}
