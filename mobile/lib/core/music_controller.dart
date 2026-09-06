import 'package:flutter/services.dart';

import 'logger.dart';

/// Hands a music request to the phone's own player over the native
/// `com.farryon/music` channel.
///
/// Best-effort by nature, and by design: Android tells us nothing about whether
/// a player acted on a media key, so a `false` here means "we could not even
/// ask" — never "the music didn't start". A failure is logged, never thrown, so
/// a missing music app can't disturb the live session.
class MusicController {
  MusicController._();

  static const MethodChannel _channel = MethodChannel('com.farryon/music');
  static final _log = Logger('MusicController');

  /// Ask a music app to search for [query] and play it. [app] may name a
  /// specific player ('spotify', 'youtube_music'); anything else lets the phone
  /// choose. Returns false when no installed app can handle it.
  static Future<bool> play(String query, {String? app}) async {
    if (query.trim().isEmpty) return false;
    try {
      final ok = await _channel.invokeMethod<bool>('playFromSearch', {
        'query': query,
        // 'default' is the tool's way of saying "you pick" — send nothing.
        'app': (app == null || app == 'default') ? null : app,
      });
      _log.info('play from search: "$query" (app: ${app ?? 'default'})');
      return ok ?? false;
    } catch (e) {
      _log.warn('play from search failed: $e');
      return false;
    }
  }

  /// Send one transport command — pause / resume / next / previous / stop — to
  /// whichever player currently holds the media session.
  static Future<bool> command(String command) async {
    try {
      final ok = await _channel.invokeMethod<bool>('mediaKey', {
        'command': command,
      });
      _log.info('media key: $command');
      return ok ?? false;
    } catch (e) {
      _log.warn('media key "$command" failed: $e');
      return false;
    }
  }
}
