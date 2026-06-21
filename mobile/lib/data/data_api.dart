import 'dart:convert';

import 'package:http/http.dart' as http;

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
}

/// Thin REST client for the backend's Notes/Tasks endpoints. Points at the same
/// backend the live session uses (via [AppConfig.httpBase]).
class DataApi {
  DataApi(this._config, {http.Client? client})
      : _client = client ?? http.Client();

  AppConfig _config;
  final http.Client _client;

  void updateConfig(AppConfig config) => _config = config;

  Uri _uri(String path) => _config.httpBase.replace(path: path);

  static const _timeout = Duration(seconds: 20);

  Future<List<NoteItem>> notes() async {
    final r = await _client.get(_uri('/notes')).timeout(_timeout);
    final list = jsonDecode(r.body) as List<dynamic>;
    return list
        .map((e) => NoteItem.fromJson(e as Map<String, dynamic>))
        .toList(growable: false);
  }

  Future<List<TaskItem>> tasks() async {
    final r = await _client.get(_uri('/tasks')).timeout(_timeout);
    final list = jsonDecode(r.body) as List<dynamic>;
    return list
        .map((e) => TaskItem.fromJson(e as Map<String, dynamic>))
        .toList(growable: false);
  }

  Future<void> setTaskDone(int id, bool done) async {
    await _client
        .post(_uri('/tasks/$id/done').replace(queryParameters: {
          'done': done.toString(),
        }))
        .timeout(_timeout);
  }

  Future<void> deleteNote(int id) async {
    await _client.delete(_uri('/notes/$id')).timeout(_timeout);
  }

  Future<void> deleteTask(int id) async {
    await _client.delete(_uri('/tasks/$id')).timeout(_timeout);
  }

  void dispose() => _client.close();
}
