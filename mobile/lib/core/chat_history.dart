import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import '../state/live_state.dart';
import 'logger.dart';

/// One saved conversation: when it happened and its transcript lines.
class ChatSession {
  const ChatSession({required this.startedAt, required this.lines});

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
        'startedAt': startedAt.toIso8601String(),
        'lines': [
          for (final l in lines) {'role': l.role, 'text': l.text},
        ],
      };

  static ChatSession fromJson(Map<String, dynamic> j) => ChatSession(
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

  /// Save the given transcript as a conversation. No-op for trivial chats
  /// (fewer than 2 lines) so accidental taps don't clutter history.
  static Future<void> saveSession(List<TranscriptEntry> transcripts) async {
    final lines = [
      for (final t in transcripts)
        if (t.text.trim().isNotEmpty) (role: t.role, text: t.text.trim()),
    ];
    if (lines.length < 2) return;
    try {
      final p = await SharedPreferences.getInstance();
      final sessions = _read(p)
        ..insert(0, ChatSession(startedAt: DateTime.now(), lines: lines));
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
