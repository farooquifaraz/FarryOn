import 'package:flutter/services.dart';

import '../core/logger.dart';

/// Lowers other apps' audio while a voice exchange is happening.
///
/// A music player keeps playing at full volume while the user talks to Farry
/// and while she answers — the app never asked the phone for audio focus, so
/// the phone had no reason to tell the player to step aside (2026-09-08, the
/// "voice command during music" issue). This asks for a TRANSIENT, MAY-DUCK
/// focus: well-behaved players lower their volume for the moment and come
/// back up when it is released. Nothing is paused, nothing is owned for long.
///
/// Every call is best-effort and logged: audio focus must never be able to
/// break a session, and the native side answers "unavailable" rather than
/// throwing.
class AudioFocus {
  AudioFocus({MethodChannel? channel})
      : _channel = channel ?? const MethodChannel('com.farryon/audio_focus');

  static final _log = Logger('AudioFocus');

  final MethodChannel _channel;

  /// Whether we currently hold the duck focus.
  bool get isHeld => _held;
  bool _held = false;

  /// Ask other audio to lower itself. Idempotent while held.
  /// Returns what the platform did: `granted` · `denied` · `unavailable`.
  Future<String> duck() async {
    if (_held) return 'granted';
    try {
      final r = await _channel.invokeMethod<String>('requestDuck');
      _held = r == 'granted';
      _log.info('audio focus (duck): ${r ?? "no answer"}');
      return r ?? 'unavailable';
    } catch (e) {
      _log.warn('requestDuck failed: $e');
      _held = false;
      return 'unavailable';
    }
  }

  /// Let other audio come back up. Safe to call any time.
  Future<void> release() async {
    if (!_held) return;
    _held = false;
    try {
      await _channel.invokeMethod<void>('abandon');
      _log.info('audio focus released');
    } catch (e) {
      _log.warn('abandon failed: $e');
    }
  }

  /// Whether ANY app is playing on the music stream right now — the phone's
  /// own answer, not a guess from which tool was last called. Our own voice
  /// playback counts too; callers subtract that themselves.
  Future<bool> isMusicActive() async {
    try {
      return await _channel.invokeMethod<bool>('isMusicActive') ?? false;
    } catch (_) {
      return false;
    }
  }
}
