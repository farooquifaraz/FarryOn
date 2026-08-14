/// Speaking the translation with the voice Android already has.
///
/// The cloud voice is the single most expensive part of live translation and
/// the slowest: of the ~$0.031 a minute costs, about $0.025 is the spoken
/// audio, and its first sound arrives roughly a second and a half after the
/// text is ready. Android ships a text-to-speech engine on every phone. It is
/// free, it is local, and it starts talking immediately.
///
/// The trade is honesty about quality: the platform voice is flatter than a
/// generated one. For a translator that is a fair swap — being understood
/// quickly matters more than sounding lifelike, and it is what keeps the
/// feature affordable enough to have a free tier at all.
///
/// Every call is best-effort. A phone with no voice data for a language must
/// not break the session: the translation is on screen either way, and
/// [speak] reports whether it was actually said so the caller can tell the
/// user once rather than failing silently forever.
library;

import 'package:flutter_tts/flutter_tts.dart';

import '../core/logger.dart';

class DeviceVoice {
  DeviceVoice({FlutterTts? tts}) : _tts = tts;

  static final _log = Logger('DeviceVoice');

  /// Built on FIRST USE, not on construction.
  ///
  /// `FlutterTts()` installs a platform method-call handler the moment it is
  /// made, which needs a binding. Creating one in the translate controller's
  /// initialiser took out thirty-two unrelated unit tests that never intended
  /// to speak. Nothing here touches the platform until someone asks for sound.
  FlutterTts? _tts;
  bool _ready = false;

  /// Languages this phone has actually refused, so the caller is told once
  /// rather than on every sentence.
  final Set<String> _unavailable = <String>{};

  /// True once [speak] has been asked for a language the phone cannot say.
  bool cannotSpeak(String language) => _unavailable.contains(_base(language));

  Future<void> initialize() async {
    if (_ready) return;
    try {
      final tts = _tts ??= FlutterTts();
      // Wait for each utterance so sentences queue behind each other instead
      // of talking over the one before.
      await tts.awaitSpeakCompletion(true);
      _ready = true;
    } catch (e) {
      _log.warn('tts init failed: $e');
    }
  }

  /// Say [text] in [language]. Returns false when the phone could not.
  Future<bool> speak(String text, String language) async {
    if (text.trim().isEmpty) return false;
    await initialize();
    final tts = _tts;
    if (tts == null) return false;
    final code = _base(language);
    try {
      final available = await tts.isLanguageAvailable(code);
      if (available != true) {
        if (_unavailable.add(code)) {
          _log.warn('no on-device voice for $code');
        }
        return false;
      }
      await tts.setLanguage(code);
      await tts.speak(text);
      return true;
    } catch (e) {
      _log.warn('speak failed: $e');
      return false;
    }
  }

  /// Stop mid-sentence — used when the session ends or the user interrupts.
  ///
  /// A no-op if nothing ever spoke: a session that ended without a translation
  /// must not be the thing that first wakes the engine.
  Future<void> stop() async {
    final tts = _tts;
    if (tts == null) return;
    try {
      await tts.stop();
    } catch (e) {
      _log.warn('stop failed: $e');
    }
  }

  /// `hi` from `hi-IN`. The engine wants a language, not a region variant, and
  /// a phone that has Hindi should not be told it lacks `hi-IN`.
  String _base(String code) => code.split(RegExp('[-_]')).first.toLowerCase();
}
