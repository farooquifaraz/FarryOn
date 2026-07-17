import 'dart:async';
import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import '../data/data_api.dart';
import 'logger.dart';

/// The last notes and tasks the server told us about, kept on the phone.
///
/// This is the read half of local-first (docs/LOCAL_FIRST_SYNC.md phase 2): the
/// screens paint from here immediately and then refresh in the background, so
/// Notes opens with no spinner and still shows your things on a plane. The
/// server stays the source of truth — nothing here is a backup, and every write
/// still goes straight to it.
///
/// **Scoped per user, and that is the whole point.** Two people can share a
/// phone, and a cache keyed on nothing would hand the second one the first
/// one's notes — the exact bug the backend was fixed for on 2026-07-15. The
/// user id is part of the key, and signing out drops everything: the data isn't
/// ours to keep once someone leaves, and the phone's storage isn't encrypted
/// yet. The server still has it all; signing back in pulls it down again.
///
/// JSON in SharedPreferences rather than SQLite, deliberately: the API caps
/// these lists at 200 rows, `chat_history.dart` already stores this way, and a
/// native database with a schema and migrations is a large thing to add for a
/// cache. Phase 3's outbox will want real rows — swap the storage behind these
/// same six functions then.
class DataCache {
  static const _notesKey = 'cache.notes.v1';
  static const _tasksKey = 'cache.tasks.v1';

  static final _log = Logger('DataCache');
  static SharedPreferences? _prefs;

  /// Call once at startup, alongside [ConfigStore.init].
  ///
  /// Assigns rather than `??=`: SharedPreferences caches its own instance, so
  /// re-fetching costs nothing, and holding the first one forever means a test
  /// that resets the store still reads the values from the test before it.
  static Future<void> init() async {
    _prefs = await SharedPreferences.getInstance();
  }

  /// A signed-out session reads and writes under `anon` — it still has data
  /// (the backend's shared anonymous user), and giving it a key of its own
  /// keeps it from colliding with a real account's.
  static String _key(String base, int? userId) =>
      '$base.${userId == null ? "anon" : "u$userId"}';

  static List<NoteItem>? notes(int? userId) => _read(
        _key(_notesKey, userId),
        NoteItem.fromJson,
      );

  static List<TaskItem>? tasks(int? userId) => _read(
        _key(_tasksKey, userId),
        TaskItem.fromJson,
      );

  static Future<void> saveNotes(int? userId, List<NoteItem> notes) =>
      _write(_key(_notesKey, userId), [
        for (final n in notes)
          {'id': n.id, 'text': n.text, 'createdAt': n.createdAt},
      ]);

  static Future<void> saveTasks(int? userId, List<TaskItem> tasks) =>
      _write(_key(_tasksKey, userId), [
        for (final t in tasks)
          {
            'id': t.id,
            'title': t.title,
            'done': t.done,
            'dueDate': t.dueDate,
            'createdAt': t.createdAt,
          },
      ]);

  /// Forget everything, for everyone. Called on sign-out.
  ///
  /// Not just the leaving user's keys: a phone that has held two accounts has
  /// two caches, and "clear mine" would leave the other sitting there for
  /// whoever picks the phone up next.
  static Future<void> clear() async {
    final p = _prefs;
    if (p == null) return;
    for (final k in p.getKeys().toList()) {
      if (k.startsWith(_notesKey) || k.startsWith(_tasksKey)) {
        await p.remove(k);
      }
    }
  }

  /// Returns null when there's nothing cached — which the caller must treat as
  /// "no answer yet", not as "you have no notes". Corrupt JSON reads as null
  /// too: a cache that can't be parsed is a cache we don't have, and throwing
  /// here would break the screen it exists to speed up.
  static List<T>? _read<T>(String key, T Function(Map<String, dynamic>) from) {
    final raw = _prefs?.getString(key);
    if (raw == null) return null;
    try {
      return [
        for (final e in jsonDecode(raw) as List<dynamic>)
          from((e as Map).cast<String, dynamic>()),
      ];
    } catch (e) {
      _log.warn('dropping unreadable cache at $key: $e');
      unawaited(_prefs!.remove(key));
      return null;
    }
  }

  static Future<void> _write(String key, List<Map<String, dynamic>> rows) async {
    try {
      await _prefs?.setString(key, jsonEncode(rows));
    } catch (e) {
      // A cache that won't save is a slow app, not a broken one.
      _log.warn('could not cache $key: $e');
    }
  }
}
