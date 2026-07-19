import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data_cache.dart';
import '../../core/outbox.dart';
import '../../core/outbox_sync.dart';
import '../../core/theme.dart';
import '../../core/ui.dart';
import '../../data/data_api.dart';
import '../../state/auth.dart';
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

  /// Paint what we have, then go and check.
  ///
  /// The cache read is synchronous, so a returning user sees their notes in the
  /// first frame — no spinner — and the refresh happens behind them. If the
  /// refresh fails but we had something cached, that is **not** an error: the
  /// notes on screen are the ones the server last gave us, and telling someone
  /// on a plane that we "couldn't load" while their notes sit right there would
  /// be a lie about a working app.
  Future<void> _load() async {
    final userId = ref.read(authProvider).userId;
    final cached = DataCache.notes(userId);
    if (cached != null && _notes == null) {
      setState(() {
        _notes = cached;
        _loading = false;
      });
    }

    setState(() {
      _loading = _notes == null;
      _error = false;
    });
    try {
      // The server is reachable, so anything queued offline can go now — and it
      // must go BEFORE the fetch, or the reply would hand back the rows we
      // just deleted and put them straight back on screen.
      await OutboxSync.drain(_api, userId);
      final n = await _api.notes();
      if (!mounted) return;
      unawaited(DataCache.saveNotes(userId, n));
      setState(() {
        _notes = n;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = _notes == null; // only if we have nothing to show
        _loading = false;
      });
    }
  }

  /// Optimistic delete: the row goes now. Offline it stays gone and the
  /// deletion is queued — see [Outbox].
  Future<void> _delete(NoteItem n) async {
    setState(() => _notes = _notes?.where((x) => x.id != n.id).toList());
    final userId = ref.read(authProvider).userId;
    // Write the shorter list through either way, or the next open reads the
    // cache and the note walks back in.
    unawaited(DataCache.saveNotes(userId, _notes ?? []));
    try {
      await _api.deleteNote(n.id);
    } on NotFoundException {
      // Already gone — deleted on another device, or Farry removed it. The row
      // is absent, which is what the tap asked for.
    } on SessionExpiredException {
      // DataApi is already signing out; the login screen is on its way.
    } catch (_) {
      // Offline. Keep the deletion on screen and queue it: rolling back would
      // put a note the user just threw away back in front of them, and it is
      // the network that failed, not the request.
      await Outbox.add(userId, OutboxOp(kind: OutboxKind.deleteNote, id: n.id));
      if (!mounted) return;
      dataSnack(context, "Deleted. Will sync when you're back online.");
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
