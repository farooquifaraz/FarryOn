import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import '../state/live_state.dart';
import 'logger.dart';

/// One saved conversation: when it happened and its transcript lines.
class ChatSession {
  ChatSession({String? id, required this.startedAt, required this.lines})
      : id = id ?? startedAt.microsecondsSinceEpoch.toString();

  /// Stable id, so the periodic autosaves of a LIVE session update this same
  /// entry instead of piling up copies.
  final String id;

  /// Local time the conversation was saved.
  final DateTime startedAt;

  /// `{role, text}` pairs in order (role is `user` or `assistant`).
  final List<({String role, String text})> lines;

  /// A short one-line preview (first user line, else first line).
  String get preview {
    final u = lines.where((l) => l.role == 'user');
    final src = u.isNotEmpty ? u.first.text : (lines.isNotEmpty ? lines.first.text : '');
    return src.isEmpty ? '(no text)' : src;
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'startedAt': startedAt.toIso8601String(),
        'lines': [
          for (final l in lines) {'role': l.role, 'text': l.text},
        ],
      };

  static ChatSession fromJson(Map<String, dynamic> j) => ChatSession(
        id: j['id'] as String?,
        startedAt:
            DateTime.tryParse(j['startedAt'] as String? ?? '') ?? DateTime.now(),
        lines: [
          for (final e in (j['lines'] as List<dynamic>? ?? const []))
            (
              role: (e as Map)['role'] as String? ?? 'assistant',
              text: e['text'] as String? ?? '',
            ),
        ],
      );
}

/// Persists past conversations on the device so the user can read them later.
/// Stored as a JSON array in `shared_preferences`, newest first, capped.
class ChatHistoryStore {
  ChatHistoryStore._();

  static final _log = Logger('ChatHistoryStore');
  static const _key = 'chat.history.v1';
  static const _maxSessions = 30;

  /// Id of the conversation currently being written, so repeated saves of a
  /// LIVE session replace its entry instead of appending duplicates.
  static String? _openId;

  /// Start a new conversation entry. Call when a live session begins; the
  /// autosaves that follow all fold into this one entry.
  static void beginSession() => _openId = null;

  /// Save the given transcript as a conversation. No-op for trivial chats
  /// (fewer than 2 lines) so accidental taps don't clutter history.
  ///
  /// Called both periodically DURING a session and at teardown: saving only
  /// on a clean disconnect lost every conversation where the app was killed,
  /// the network dropped, or the session expired (user-reported 2026-08-05:
  /// "this conversation is not in the conversation cards"). Re-saving updates
  /// the same entry via [_openId].
  static Future<void> saveSession(List<TranscriptEntry> transcripts) async {
    final lines = [
      for (final t in transcripts)
        if (t.text.trim().isNotEmpty) (role: t.role, text: t.text.trim()),
    ];
    if (lines.length < 2) return;
    try {
      final p = await SharedPreferences.getInstance();
      final sessions = _read(p);
      final openId = _openId;
      final existing =
          openId == null ? -1 : sessions.indexWhere((s) => s.id == openId);
      if (existing >= 0) {
        // Same live session: replace in place, keeping its original start time
        // (and its position, so an updating chat doesn't jump around).
        sessions[existing] = ChatSession(
          id: openId!,
          startedAt: sessions[existing].startedAt,
          lines: lines,
        );
      } else {
        final entry = ChatSession(startedAt: DateTime.now(), lines: lines);
        _openId = entry.id;
        sessions.insert(0, entry);
      }
      while (sessions.length > _maxSessions) {
        sessions.removeLast();
      }
      await p.setString(
        _key,
        jsonEncode([for (final s in sessions) s.toJson()]),
      );
      _log.info('saved conversation (${lines.length} lines)');
    } catch (e) {
      _log.warn('save chat history failed: $e');
    }
  }

  static Future<List<ChatSession>> load() async {
    try {
      return _read(await SharedPreferences.getInstance());
    } catch (e) {
      _log.warn('load chat history failed: $e');
      return const [];
    }
  }

  static Future<void> clear() async {
    final p = await SharedPreferences.getInstance();
    await p.remove(_key);
  }

  /// Remove a single saved conversation, matched by its [ChatSession.startedAt]
  /// timestamp (unique per save) so it's robust to list reordering. No-op if no
  /// session matches.
  static Future<void> deleteSession(DateTime startedAt) async {
    final p = await SharedPreferences.getInstance();
    final key = startedAt.toIso8601String();
    final sessions = _read(p)
      ..removeWhere((s) => s.startedAt.toIso8601String() == key);
    await p.setString(
      _key,
      jsonEncode([for (final s in sessions) s.toJson()]),
    );
  }

  static List<ChatSession> _read(SharedPreferences p) {
    final raw = p.getString(_key);
    if (raw == null || raw.isEmpty) return [];
    final list = jsonDecode(raw) as List<dynamic>;
    return [
      for (final e in list) ChatSession.fromJson(e as Map<String, dynamic>),
    ];
  }
}
