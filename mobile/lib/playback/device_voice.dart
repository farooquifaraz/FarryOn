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

import 'dart:async';

import 'package:flutter/services.dart';
import 'package:flutter_tts/flutter_tts.dart';

import '../core/logger.dart';

class DeviceVoice {
  DeviceVoice({
    FlutterTts? tts,
    MethodChannel? channel,
    this.budgetFloor = const Duration(milliseconds: 1500),
    this.budgetPerCharacter = const Duration(milliseconds: 90),
  })  : _tts = tts,
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

  /// The longest [speak] waits for the engine to say it has finished: a
  /// floor for the engine to start, plus this much per character of text.
  ///
  /// The plugin's completion is a promise the engine does not always keep.
  /// In flutter_tts 4.2.5 (Android) the `speak` result is answered only from
  /// `onDone`; `onStop` (interrupted — a call, another app taking the audio
  /// focus) and `onError` (the engine choking on a sentence) clear the
  /// plugin's own "speaking" flag and leave the Dart future hanging forever.
  /// The count above then never came back down, the echo guard treated the
  /// phone as still talking, and every later utterance from the user was
  /// dropped before it reached the server: one translation per session, then
  /// "listening" for as long as the user cared to wait (Vivo, 2026-09-30).
  /// The budget is generous — real speech is ~15 characters a second, this
  /// allows ~11 — because reopening the mic while the phone is still talking
  /// puts the echo loop back, and that is the worse failure.
  final Duration budgetFloor;
  final Duration budgetPerCharacter;

  /// Utterances say their piece in the order they arrived. The plugin refuses
  /// (returns 0, says nothing) a sentence that arrives while the one before
  /// is still playing, so two translations in quick succession — exactly
  /// what fast speech produces — used to lose the second one. The count is
  /// still raised while a sentence waits its turn: the phone is going to be
  /// talking, so the microphone stays untrusted throughout.
  Future<void> _turn = Future<void>.value();

  /// Completed from the plugin's cancel and error handlers, so an utterance
  /// the engine gave up on releases the microphone at once rather than at the
  /// end of its budget.
  Completer<void>? _abandoned;

  /// Bumped by [stop], so sentences still queued behind it stay unsaid.
  int _stops = 0;

  Duration budgetFor(String text) =>
      budgetFloor + budgetPerCharacter * text.length;

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
      // Neither of these answers the pending `speak` future on Android; they
      // are the only word we get that the sentence will not be finished.
      tts.setCancelHandler(() => _giveUp('cancelled by the engine'));
      tts.setErrorHandler((msg) => _giveUp('engine error: $msg'));
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
      // Counted around the call, not after it. `awaitSpeakCompletion(true)`
      // makes this return when the utterance ends, so the window it brackets
      // is exactly the window in which the microphone must not be trusted.
      _speaking++;
      try {
        final previous = _turn;
        final mine = Completer<void>();
        _turn = mine.future;
        final session = _stops;
        try {
          await previous;
          // A sentence that waited its turn through a [stop] belongs to a
          // session that has ended; it is not owed to the room now.
          if (_stops != session) return true;
          await tts.setLanguage(code);
          await _sayBounded(tts, text);
        } finally {
          mine.complete();
        }
      } finally {
        // [stop] may already have zeroed the count under a queued sentence.
        if (_speaking > 0) _speaking--;
        _stoppedAt = DateTime.now();
      }
      return true;
    } catch (e) {
      _log.warn('speak failed: $e');
      return false;
    }
  }

  /// One utterance, and a promise that this returns whatever the engine does.
  Future<void> _sayBounded(FlutterTts tts, String text) async {
    final abandoned = _abandoned = Completer<void>();
    final budget = budgetFor(text);
    try {
      await Future.any<void>([
        tts.speak(text).then((_) {}),
        abandoned.future,
      ]).timeout(budget);
    } on TimeoutException {
      _log.warn('engine never reported the end of a ${text.length}-char '
          'utterance within ${budget.inSeconds}s; releasing the microphone');
      // Whatever is (or is not) still coming out of the speaker, the plugin's
      // own "speaking" flag must not refuse the next sentence too.
      try {
        await tts.stop();
      } catch (_) {}
    } finally {
      if (identical(_abandoned, abandoned)) _abandoned = null;
    }
  }

  void _giveUp(String why) {
    final abandoned = _abandoned;
    if (abandoned == null || abandoned.isCompleted) return;
    _log.warn('utterance $why');
    abandoned.complete();
  }

  /// Stop mid-sentence — used when the session ends or the user interrupts.
  ///
  /// A no-op if nothing ever spoke: a session that ended without a translation
  /// must not be the thing that first wakes the engine.
  Future<void> stop() async {
    final tts = _tts;
    if (tts == null) return;
    _stops++;
    _giveUp('stopped');
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
