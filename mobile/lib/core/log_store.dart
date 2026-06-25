import 'package:flutter/foundation.dart';

/// One captured log line.
@immutable
class LogEntry {
  const LogEntry({
    required this.time,
    required this.level,
    required this.tag,
    required this.message,
    this.provider,
  });

  final DateTime time;
  final String level; // DEBUG | INFO | WARN | ERROR
  final String tag; // subsystem, e.g. "LiveController"
  final String message;

  /// Active AI provider/model at the time, e.g. "gemini" / "gpt-realtime".
  final String? provider;

  /// `12:30:45.123  WARN  [gemini] LiveController: message`
  String format() {
    final t = time.toIso8601String().split('T').last; // HH:MM:SS.mmm...
    final p = (provider == null || provider!.isEmpty) ? '' : '[$provider] ';
    return '$t  ${level.padRight(5)} $p$tag: $message';
  }
}

/// In-app ring buffer of recent log lines, so the user can SHARE a debug trail
/// instead of taking screenshots. The [Logger] feeds every line here, and the
/// live session stamps the active AI provider/model so each line shows which AI
/// was in use.
///
/// Pure singleton, no plugins. UI listens to [revision] to refresh.
class LogStore {
  LogStore._();
  static final LogStore instance = LogStore._();

  /// Hard cap so a long session can't grow memory without bound.
  static const int _maxEntries = 1500;

  final List<LogEntry> _entries = <LogEntry>[];

  /// Active AI provider/model, set by the live session on `ready`. Stamped onto
  /// every entry so the shared log says which AI the user was talking to.
  String? currentProvider;

  /// Bumped on every change so a `ValueListenableBuilder` can rebuild the view.
  final ValueNotifier<int> revision = ValueNotifier<int>(0);

  /// Snapshot of the buffered entries (oldest first).
  List<LogEntry> get entries => List.unmodifiable(_entries);

  int get length => _entries.length;

  /// Append a line. Called by [Logger]; safe to call from anywhere.
  void add(String level, String tag, String message) {
    _entries.add(LogEntry(
      time: DateTime.now(),
      level: level,
      tag: tag,
      message: message,
      provider: currentProvider,
    ));
    if (_entries.length > _maxEntries) {
      _entries.removeRange(0, _entries.length - _maxEntries);
    }
    revision.value++;
  }

  /// Record which AI is now live (shown on every subsequent line + the header).
  void setProvider(String? provider) {
    currentProvider = provider;
    add('INFO', 'Session', 'AI provider set to ${provider ?? "default"}');
  }

  void clear() {
    _entries.clear();
    revision.value++;
  }

  /// The full log as shareable text, newest context at the top header.
  String export({String? appVersion, String? device}) {
    final b = StringBuffer()
      ..writeln('FarryOn debug log')
      ..writeln('exported: ${DateTime.now().toIso8601String()}')
      ..writeln('ai provider: ${currentProvider ?? "n/a"}')
      ..writeln('app: ${appVersion ?? "n/a"}   device: ${device ?? "n/a"}')
      ..writeln('lines: ${_entries.length}')
      ..writeln('-' * 48);
    for (final e in _entries) {
      b.writeln(e.format());
    }
    return b.toString();
  }
}
