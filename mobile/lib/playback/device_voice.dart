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

import 'package:flutter/services.dart';
import 'package:flutter_tts/flutter_tts.dart';

import '../core/logger.dart';

class DeviceVoice {
  DeviceVoice({FlutterTts? tts, MethodChannel? channel})
      : _tts = tts,
        _channel = channel ?? const MethodChannel('com.farryon/voice_data');

  static final _log = Logger('DeviceVoice');

  /// Built on FIRST USE, not on construction.
  ///
  /// `FlutterTts()` installs a platform method-call handler the moment it is
  /// made, which needs a binding. Creating one in the translate controller's
  /// initialiser took out thirty-two unrelated unit tests that never intended
  /// to speak. Nothing here touches the platform until someone asks for sound.
  FlutterTts? _tts;
  final MethodChannel _channel;
  bool _ready = false;

  /// Base codes this phone can actually say, once asked. Null until then.
  Set<String>? _installed;

  /// Languages this phone has actually refused, so the caller is told once
  /// rather than on every sentence.
  final Set<String> _unavailable = <String>{};

  /// True once [speak] has been asked for a language the phone cannot say.
  bool cannotSpeak(String language) => _unavailable.contains(_base(language));

  /// Utterances started and not yet finished. A count, not a flag, because
  /// sentences are spoken without waiting for the one before: two can overlap,
  /// and the first one finishing must not declare the phone silent.
  int _speaking = 0;

  /// When the last utterance finished, so the caller can allow for the room's
  /// ring-down after it.
  DateTime? _stoppedAt;

  /// True while this phone is talking, plus [tail] for the room to go quiet.
  ///
  /// The echo guard in the translate controller asks the PCM player this same
  /// question, and used to ask only the PCM player. Moving the voice on-device
  /// meant the translation no longer went through that player at all, so the
  /// guard saw silence and the microphone stayed open through every spoken
  /// sentence. The phone then heard itself: "and Uncle Javed" came back as
  /// "एंड अंकल जावेद" — English written in Devanagari, translated again, paid
  /// for again (device-seen 2026-08-14).
  bool isSpeakingWithin(Duration tail) {
    if (_speaking > 0) return true;
    final stopped = _stoppedAt;
    return stopped != null && DateTime.now().isBefore(stopped.add(tail));
  }

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
      // Counted around the call, not after it. `awaitSpeakCompletion(true)`
      // makes this return when the utterance ends, so the window it brackets
      // is exactly the window in which the microphone must not be trusted.
      _speaking++;
      try {
        await tts.speak(text);
      } finally {
        _speaking--;
        _stoppedAt = DateTime.now();
      }
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
    } finally {
      // Whatever the engine does with the pending futures, nothing is coming
      // out of the speaker now. Leaving the count raised would hold the
      // microphone shut for the rest of the session.
      _speaking = 0;
      _stoppedAt = DateTime.now();
    }
  }

  /// Which languages this phone can speak, as base codes (`hi`, `ar`).
  ///
  /// A phone only has the voices someone installed, so a target the engine
  /// cannot say leaves the translation on screen and silent. Knowing this in
  /// advance is what lets the picker say so BEFORE a conversation starts,
  /// rather than halfway through one.
  ///
  /// Returns an empty set if the engine cannot be asked — callers treat that
  /// as "unknown", never as "nothing works".
  Future<Set<String>> installedLanguages() async {
    final cached = _installed;
    if (cached != null) return cached;
    await initialize();
    final tts = _tts;
    if (tts == null) return const <String>{};
    try {
      final raw = await tts.getLanguages;
      final codes = <String>{
        for (final entry in (raw as List? ?? const []))
          _base(entry.toString()),
      }..removeWhere((c) => c.isEmpty);
      _installed = codes;
      return codes;
    } catch (e) {
      _log.warn('could not list voices: \$e');
      return const <String>{};
    }
  }

  /// Open the screen where a voice can be added. False if the phone has none.
  ///
  /// Deliberately does not download anything: voice data runs to tens of
  /// megabytes, often on mobile data, and that is the user's decision.
  Future<bool> openVoiceInstaller() async {
    for (final method in ['installVoices', 'openSettings']) {
      try {
        final ok = await _channel.invokeMethod<bool>(method);
        if (ok == true) return true;
      } catch (e) {
        _log.warn('\$method failed: \$e');
      }
    }
    return false;
  }

  /// `hi` from `hi-IN`. The engine wants a language, not a region variant, and
  /// a phone that has Hindi should not be told it lacks `hi-IN`.
  String _base(String code) => code.split(RegExp('[-_]')).first.toLowerCase();
}
