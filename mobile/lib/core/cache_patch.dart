import 'dart:async';

import '../data/data_api.dart';
import '../protocol/messages.dart';
import 'data_cache.dart';

/// Fold a note or task Farry just created or removed into the phone's cache.
///
/// Phase 4 of docs/LOCAL_FIRST_SYNC.md. Farry writes server-side — "yaad rakho,
/// dentist Tuesday" creates the row without the phone being involved — so her
/// notes only showed up after a manual refresh. The `tool_result` already
/// arrives over the WebSocket in about a second; this stops throwing it away.
///
/// A top-level function rather than a method on LiveController so it can be
/// tested without an audio engine, a camera and a glasses channel in tow.
///
/// Deliberately narrow. It patches the cache and nothing else: an open screen
/// refetches on its own, and if a patch is ever wrong the next real fetch
/// overwrites it. A cache is allowed to be briefly stale — it is not allowed to
/// be the reason a note goes missing, so nothing here deletes on a guess.
void applyToolResultToCache(ToolResultMessage msg, int? userId) {
  if (!msg.ok) return;
  final res = msg.result;
  final id = (res?['id'] as num?)?.toInt();
  if (id == null) return;

  switch (msg.name) {
    case 'create_note':
      final cached = DataCache.notes(userId);
      // null means we have never fetched. Seeding a one-note cache from here
      // would tell the Notes screen "this is everything you have" and hide the
      // rest until something forced a refresh.
      if (cached == null) return;
      unawaited(DataCache.saveNotes(userId, [
        NoteItem(
          id: id,
          text: (res?['text'] as String?) ?? '',
          createdAt: res?['createdAt'] as String?,
        ),
        // Filtered so a redelivered tool_result doesn't show the same note
        // twice — which reads as a bug in Farry rather than in us.
        ...cached.where((n) => n.id != id),
      ]));

    case 'create_task':
      final cached = DataCache.tasks(userId);
      if (cached == null) return;
      unawaited(DataCache.saveTasks(userId, [
        TaskItem(
          id: id,
          title: (res?['title'] as String?) ?? '',
          done: (res?['done'] as bool?) ?? false,
          dueDate: res?['due_date'] as String?,
          createdAt: res?['createdAt'] as String?,
        ),
        ...cached.where((t) => t.id != id),
      ]));

    case 'complete_task':
      final cached = DataCache.tasks(userId);
      if (cached == null) return;
      unawaited(DataCache.saveTasks(userId, [
        for (final t in cached)
          if (t.id == id) t.copyWith(done: true) else t,
      ]));

    case 'delete_note':
      final cached = DataCache.notes(userId);
      if (cached == null) return;
      unawaited(
        DataCache.saveNotes(userId, cached.where((n) => n.id != id).toList()),
      );

    case 'delete_task':
      final cached = DataCache.tasks(userId);
      if (cached == null) return;
      unawaited(
        DataCache.saveTasks(userId, cached.where((t) => t.id != id).toList()),
      );

    // update_task is left out on purpose: its result carries only the fields
    // that changed, so patching from it would blank whatever it didn't mention.
    // The screen's own refetch handles that one.
  }
}
