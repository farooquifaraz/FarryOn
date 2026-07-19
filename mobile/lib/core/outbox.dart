import 'dart:async';
import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import 'logger.dart';

/// The one change the phone made that the server hasn't heard about yet.
enum OutboxKind { deleteNote, deleteTask, setTaskDone }

class OutboxOp {
  const OutboxOp({
    required this.kind,
    required this.id,
    this.done = false,
    this.queuedAt,
  });

  final OutboxKind kind;

  /// The **server's** row id. Every operation the app can queue acts on a row
  /// that already exists server-side, which is what makes this queue simple —
  /// see the note on [Outbox].
  final int id;

  /// Only meaningful for [OutboxKind.setTaskDone].
  final bool done;

  final String? queuedAt;

  /// Two ops collapse if they target the same row and kind — the later one
  /// wins. Ticking a task three times only needs sending once.
  String get slot => '${kind.name}:$id';

  Map<String, dynamic> toJson() => {
        'kind': kind.name,
        'id': id,
        'done': done,
        'queuedAt': queuedAt,
      };

  static OutboxOp? fromJson(Map<String, dynamic> j) {
    final kind = OutboxKind.values
        .where((k) => k.name == j['kind'])
        .firstOrNull;
    final id = (j['id'] as num?)?.toInt();
    // A row we can't understand is dropped rather than retried forever — it
    // came from an older build's format, and the server is still the source of
    // truth for whatever it described.
    if (kind == null || id == null) return null;
    return OutboxOp(
      kind: kind,
      id: id,
      done: j['done'] as bool? ?? false,
      queuedAt: j['queuedAt'] as String?,
    );
  }
}

/// Changes made while the server was unreachable, waiting to be sent.
///
/// Phase 3 of docs/LOCAL_FIRST_SYNC.md, and much smaller than that document
/// assumed. It planned for offline *creates*, with client-generated UUIDs so a
/// note made on a plane could be pushed without a server id. But the app has no
/// way to create a note: adding is voice-only, and Farry runs server-side, so
/// creating one already requires the network. **Everything the app can queue —
/// delete a note, delete a task, tick a task — acts on a row that already
/// exists with a real server id.**
///
/// That removes the two hard parts. No client_id is needed here (the column
/// still earns its place for a future editor), and every operation is naturally
/// idempotent: deleting twice leaves the row deleted, ticking twice leaves it
/// ticked. Re-sending after a crash mid-drain is therefore safe.
///
/// Scoped per user like [DataCache], for the same reason: one phone, two people.
class Outbox {
  static const _key = 'outbox.v1';
  static final _log = Logger('Outbox');
  static SharedPreferences? _prefs;

  static Future<void> init() async {
    _prefs = await SharedPreferences.getInstance();
  }

  static String _keyFor(int? userId) =>
      '$_key.${userId == null ? "anon" : "u$userId"}';

  static List<OutboxOp> pending(int? userId) {
    final raw = _prefs?.getString(_keyFor(userId));
    if (raw == null) return const [];
    try {
      return [
        for (final e in jsonDecode(raw) as List<dynamic>)
          if (OutboxOp.fromJson((e as Map).cast<String, dynamic>())
              case final op?)
            op,
      ];
    } catch (e) {
      _log.warn('dropping unreadable outbox: $e');
      unawaited(_prefs!.remove(_keyFor(userId)));
      return const [];
    }
  }

  /// Queue a change, replacing any earlier one for the same row and kind.
  ///
  /// Collapsing matters more than it looks: without it, tapping a checkbox on
  /// and off for a minute on a train queues sixty requests that all say the
  /// same thing once the last one lands.
  static Future<void> add(int? userId, OutboxOp op) async {
    final queued = OutboxOp(
      kind: op.kind,
      id: op.id,
      done: op.done,
      queuedAt: DateTime.now().toUtc().toIso8601String(),
    );
    final kept = [
      for (final existing in pending(userId))
        if (existing.slot != queued.slot) existing,
      queued,
    ];
    await _write(userId, kept);
  }

  static Future<void> remove(int? userId, OutboxOp op) async {
    await _write(
      userId,
      [for (final e in pending(userId)) if (e.slot != op.slot) e],
    );
  }

  /// Forget everything, for every account. Called on sign-out, alongside
  /// [DataCache.clear] — a queue of changes belongs to whoever made them, and
  /// they are not ours to replay for the next person to use the phone.
  static Future<void> clear() async {
    final p = _prefs;
    if (p == null) return;
    for (final k in p.getKeys().toList()) {
      if (k.startsWith(_key)) await p.remove(k);
    }
  }

  static Future<void> _write(int? userId, List<OutboxOp> ops) async {
    try {
      await _prefs?.setString(
        _keyFor(userId),
        jsonEncode([for (final o in ops) o.toJson()]),
      );
    } catch (e) {
      // Losing the queue means the change reaches the server late (on the next
      // real fetch, the server's version wins) rather than the app breaking.
      _log.warn('could not save outbox: $e');
    }
  }
}

extension _FirstOrNull<T> on Iterable<T> {
  T? get firstOrNull => isEmpty ? null : first;
}
