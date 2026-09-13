/// Speak for me: typed text, said out loud by the phone's own voice.
///
/// For someone who cannot speak. They type; the phone says it — and when the
/// glasses are bonded, the sound comes out in whoever is wearing them, which
/// is the point of handing the glasses to the other person. No server, no
/// cost, no network: `DeviceVoice` is the same engine live translation already
/// trusts, and it plays on Android's media route, which is where the glasses
/// sit.
///
/// Kept out of the live session on purpose. The assistant's microphone would
/// hear the phone's voice as the user talking; the screen that owns this
/// controller disconnects the assistant while it is open, exactly as the
/// translate screen does.
library;

import 'dart:async';

import '../../core/config.dart';
import '../../core/logger.dart';
import '../../playback/device_voice.dart';
import '../translate/translate_languages.dart';

/// One thing the user asked the phone to say.
class SpokenLine {
  const SpokenLine(
      {required this.text, required this.at, required this.spoken});

  final String text;
  final DateTime at;

  /// False when the phone had no voice for the language: the line is still
  /// kept — it can be shown on screen — but it was never heard.
  final bool spoken;
}

/// Immutable snapshot the screen renders.
class SpeakForMeState {
  const SpeakForMeState({
    this.language = 'en',
    this.speaking = false,
    this.lines = const [],
    this.error,
  });

  /// BCP-47 code the text is spoken in.
  final String language;

  /// True while an utterance is in flight.
  final bool speaking;

  /// Newest first. Capped by the controller.
  final List<SpokenLine> lines;

  /// A message for the user. Cleared by the next successful line.
  final String? error;

  /// The most recent line, for the big-text "show them" view.
  SpokenLine? get latest => lines.isEmpty ? null : lines.first;

  SpeakForMeState copyWith({
    String? language,
    bool? speaking,
    List<SpokenLine>? lines,
    String? error,
    bool clearError = false,
  }) =>
      SpeakForMeState(
        language: language ?? this.language,
        speaking: speaking ?? this.speaking,
        lines: lines ?? this.lines,
        error: clearError ? null : (error ?? this.error),
      );
}

class SpeakForMeController {
  SpeakForMeController({required String language, DeviceVoice? voice})
      : _voice = voice ?? DeviceVoice(),
        _state = SpeakForMeState(language: language);

  static final _log = Logger('SpeakForMe');

  /// Older lines fall off past this. The screen is a conversation aid, not a
  /// record; pinned phrases are what the user keeps.
  static const int maxLines = 50;

  final DeviceVoice _voice;
  final _stateController = StreamController<SpeakForMeState>.broadcast();
  Stream<SpeakForMeState> get stateStream => _stateController.stream;

  SpeakForMeState _state;
  SpeakForMeState get state => _state;

  bool _disposed = false;

  void _emit(SpeakForMeState next) {
    _state = next;
    if (!_stateController.isClosed) _stateController.add(next);
  }

  /// Say [text]. Returns false when nothing was said.
  ///
  /// A new line while one is still playing **replaces** it: the user pressed
  /// Speak again because the first line was wrong or the moment moved on, and
  /// queueing would make the phone finish saying the thing they retracted.
  Future<bool> say(String text) async {
    final line = text.trim();
    if (_disposed || line.isEmpty) return false;
    if (_state.speaking) await _voice.stop();
    _emit(_state.copyWith(speaking: true, clearError: true));
    var spoken = false;
    try {
      spoken = await _voice.speak(line, _state.language);
    } catch (e) {
      _log.warn('speak failed: $e');
    }
    if (_disposed) return spoken;
    final lines = [
      SpokenLine(text: line, at: DateTime.now(), spoken: spoken),
      ..._state.lines,
    ];
    if (lines.length > maxLines) lines.removeRange(maxLines, lines.length);
    _emit(_state.copyWith(
      speaking: false,
      lines: lines,
      error: spoken
          ? null
          : 'This phone has no ${translateLanguageName(_state.language)} '
              'voice yet. Add one, or pick another language.',
    ));
    return spoken;
  }

  /// Stop mid-sentence.
  Future<void> stop() async {
    await _voice.stop();
    if (!_disposed) _emit(_state.copyWith(speaking: false));
  }

  /// Change the language the text is spoken in.
  void setLanguage(String code) {
    if (code.isEmpty || code == _state.language) return;
    // The old "no voice for X" no longer applies to Y.
    _emit(_state.copyWith(language: code, clearError: true));
  }

  Future<void> dispose() async {
    if (_disposed) return;
    _disposed = true;
    await _voice.stop();
    await _stateController.close();
  }
}

/// The language Speak-for-me should start in.
///
/// The saved choice wins. Failing that, the primary language from Settings
/// ("Urdu") is mapped to its code so a user who set the app up in Urdu is
/// spoken for in Urdu without being asked twice. English is the last resort,
/// never the first.
String defaultSpeakLanguage(AppConfig cfg) {
  if (cfg.speakLanguage.isNotEmpty) return cfg.speakLanguage;
  final wanted = cfg.primaryLanguage.trim().toLowerCase();
  for (final l in kTranslateLanguages) {
    if (l.name.toLowerCase() == wanted) return l.code;
  }
  return 'en';
}

/// Phrases every install starts with. Short, because they are said in a
/// hurry, and about the situation of not being able to speak, because that
/// is the one thing every user of this screen has in common.
const List<String> kStarterPhrases = [
  'Yes',
  'No',
  'Thank you',
  'Please wait a moment',
  "I can't speak. I'll type what I want to say.",
  "Please speak slowly, I'm reading.",
  'Can you help me?',
  'Where is the bathroom?',
  'How much is this?',
  'I need a doctor',
];
