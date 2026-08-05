import 'package:flutter/services.dart';

import '../core/logger.dart';

/// Puts the phone on its voice-call audio path while a live session runs.
///
/// The platform echo canceller is attached to our microphone, but it can only
/// subtract the assistant's voice if capture and playback share the voice path
/// — otherwise the residue comes back as fake "user" turns. The native side
/// owns the policy (and skips the change entirely when audio is going to
/// Bluetooth glasses/earbuds or a wired headset); this is just the switch.
///
/// Every call is best-effort: audio routing must never be able to break a
/// session, so failures are logged and swallowed.
class VoiceAudioMode {
  VoiceAudioMode({MethodChannel? channel})
      : _channel = channel ?? const MethodChannel('com.farryon/audio_mode');

  static final _log = Logger('VoiceAudioMode');

  final MethodChannel _channel;

  /// Whether the voice path is currently applied BY US.
  bool get isActive => _active;
  bool _active = false;

  /// Ask for the voice-call path. Returns what the platform did:
  /// `applied` · `skipped_external_route` · `unavailable`.
  Future<String> enter() async {
    try {
      final r = await _channel.invokeMethod<String>('enterVoiceMode');
      _active = r == 'applied';
      _log.info('voice audio path: ${r ?? "no answer"}');
      return r ?? 'unavailable';
    } catch (e) {
      _log.warn('enterVoiceMode failed: $e');
      _active = false;
      return 'unavailable';
    }
  }

  /// Give the phone back its normal audio path. Safe to call any time.
  Future<void> exit() async {
    try {
      await _channel.invokeMethod<void>('exitVoiceMode');
    } catch (e) {
      _log.warn('exitVoiceMode failed: $e');
    } finally {
      _active = false;
    }
  }
}
