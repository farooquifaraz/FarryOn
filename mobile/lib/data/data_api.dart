import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;
import 'package:http/io_client.dart';

import '../core/config.dart';

/// A saved note returned by `GET /notes`.
class NoteItem {
  const NoteItem({required this.id, required this.text, this.createdAt});
  final int id;
  final String text;
  final String? createdAt;

  factory NoteItem.fromJson(Map<String, dynamic> j) => NoteItem(
        id: (j['id'] as num).toInt(),
        text: j['text'] as String? ?? '',
        createdAt: j['createdAt'] as String?,
      );
}

/// A to-do task returned by `GET /tasks`.
class TaskItem {
  const TaskItem({
    required this.id,
    required this.title,
    required this.done,
    this.dueDate,
    this.createdAt,
  });
  final int id;
  final String title;
  final bool done;
  final String? dueDate;
  final String? createdAt;

  factory TaskItem.fromJson(Map<String, dynamic> j) => TaskItem(
        id: (j['id'] as num).toInt(),
        title: j['title'] as String? ?? '',
        done: j['done'] as bool? ?? false,
        dueDate: j['dueDate'] as String?,
        createdAt: j['createdAt'] as String?,
      );

  TaskItem copyWith({bool? done}) => TaskItem(
        id: id,
        title: title,
        done: done ?? this.done,
        dueDate: dueDate,
        createdAt: createdAt,
      );
}

/// Thin REST client for the backend's Notes/Tasks endpoints. Points at the same
/// backend the live session uses (via [AppConfig.httpBase]).
class DataApi {
  DataApi(this._config, {http.Client? client})
      : _client = client ?? _defaultClient();

  /// An unreachable backend must fail fast: with only the overall [_timeout],
  /// a dead host left the Notes/Reminders screens on a bare spinner for the
  /// full 20s (connection-refused returns quickly, but a dropped SYN — phone
  /// off the LAN, wrong host — does not). [_connectTimeout] bounds the TCP
  /// connect alone, so "can't reach it" surfaces in seconds while a slow but
  /// *live* backend (e.g. a cold Render dyno) still gets the full budget to
  /// respond.
  static http.Client _defaultClient() =>
      IOClient(HttpClient()..connectionTimeout = _connectTimeout);

  AppConfig _config;
  final http.Client _client;

  void updateConfig(AppConfig config) => _config = config;

  Uri _uri(String path) => _config.httpBase.replace(path: path);

  /// Bearer header when a FarryOn session token exists. This is what makes the
  /// Notes/Tasks screens show *this* user's rows: the backend scopes every read
  /// and write to whoever this token names, and a request without it reaches
  /// only the anonymous pile (locally) or 401s (in production).
  Map<String, String> get _headers {
    final token = _config.authToken;
    return (token == null || token.isEmpty)
        ? const {}
        : {'Authorization': 'Bearer $token'};
  }

  /// Bound on the TCP connect only — see [_defaultClient].
  static const _connectTimeout = Duration(seconds: 5);

  /// Bound on the whole request. Stays generous so a cold cloud backend that
  /// *is* answering isn't cut off mid-response.
  static const _timeout = Duration(seconds: 20);

  Future<List<NoteItem>> notes() async {
    final r =
        await _client.get(_uri('/notes'), headers: _headers).timeout(_timeout);
    final list = jsonDecode(r.body) as List<dynamic>;
    return list
        .map((e) => NoteItem.fromJson(e as Map<String, dynamic>))
        .toList(growable: false);
  }

  Future<List<TaskItem>> tasks() async {
    final r =
        await _client.get(_uri('/tasks'), headers: _headers).timeout(_timeout);
    final list = jsonDecode(r.body) as List<dynamic>;
    return list
        .map((e) => TaskItem.fromJson(e as Map<String, dynamic>))
        .toList(growable: false);
  }

  Future<void> setTaskDone(int id, bool done) async {
    await _client
        .post(
          _uri('/tasks/$id/done').replace(queryParameters: {
            'done': done.toString(),
          }),
          headers: _headers,
        )
        .timeout(_timeout);
  }

  Future<void> deleteNote(int id) async {
    await _client
        .delete(_uri('/notes/$id'), headers: _headers)
        .timeout(_timeout);
  }

  Future<void> deleteTask(int id) async {
    await _client
        .delete(_uri('/tasks/$id'), headers: _headers)
        .timeout(_timeout);
  }

  void dispose() => _client.close();
}
