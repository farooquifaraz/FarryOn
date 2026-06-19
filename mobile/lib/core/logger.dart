import 'dart:developer' as developer;

import 'package:flutter/foundation.dart';

/// Severity levels, ordered ascending.
enum LogLevel { debug, info, warn, error }

/// A tiny, dependency-free logger.
///
/// Wraps `dart:developer`'s `log` (which integrates with DevTools and avoids the
/// noise of bare `print`). In release builds everything below [minLevel] is
/// dropped; the default keeps `info` and above.
///
/// Typical use: create one per subsystem, e.g.
/// `static final _log = Logger('LiveClient');`
class Logger {
  Logger(this.name);

  /// Subsystem name, shown as the log record's `name`.
  final String name;

  /// Global minimum level. Debug in debug builds, info otherwise.
  static LogLevel minLevel = kReleaseMode ? LogLevel.info : LogLevel.debug;

  void debug(String message) => _log(LogLevel.debug, message);
  void info(String message) => _log(LogLevel.info, message);
  void warn(String message) => _log(LogLevel.warn, message);

  void error(String message, [Object? error, StackTrace? stack]) =>
      _log(LogLevel.error, message, error, stack);

  void _log(
    LogLevel level,
    String message, [
    Object? error,
    StackTrace? stack,
  ]) {
    if (level.index < minLevel.index) return;
    developer.log(
      message,
      name: 'farryon.$name',
      level: _devLevel(level),
      error: error,
      stackTrace: stack,
    );
  }

  // Map onto dart:developer's loosely dart:logging-compatible numeric levels.
  static int _devLevel(LogLevel level) => switch (level) {
        LogLevel.debug => 500,
        LogLevel.info => 800,
        LogLevel.warn => 900,
        LogLevel.error => 1000,
      };
}
