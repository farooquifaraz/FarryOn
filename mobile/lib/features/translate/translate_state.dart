/// State for the live-translation screen.
///
/// Deliberately kept out of `state/live_state.dart`: live translation runs on
/// its own socket with its own player, and keeping its state separate is what
/// makes that isolation checkable rather than merely intended. Nothing here is
/// read by the assistant path.
library;

/// Where the translate session is in its lifecycle.
enum TranslateStatus {
  /// Not connected; the screen is showing the start button.
  idle,

  /// Socket opening / waiting for `ready`.
  starting,

  /// Connected and streaming the microphone.
  listening,

  /// The link dropped and is being re-established. Transcript is kept.
  reconnecting,

  /// Stopped by the user, or by a failure that ended the session.
  stopped,
}

/// One utterance: what was heard, and what it became.
class TranslateTurn {
  const TranslateTurn({
    required this.heard,
    this.heardLang,
    this.translated = '',
    this.heardFinal = false,
    this.sameLanguage = false,
    this.id,
    this.targetLang,
  });

  /// The language [translated] is in. Kept on the turn because the target can
  /// be changed mid-session: without it every earlier card was relabelled
  /// with the new language's name the moment the user switched.
  final String? targetLang;

  /// Which sentence this is, as the server numbered it.
  ///
  /// Sentences are translated concurrently and the speaker does not wait, so
  /// a translation usually arrives after the NEXT sentence is already on
  /// screen. Matching on this is what puts each translation under the words it
  /// belongs to; without it the first sentence of a paragraph lost its
  /// translation entirely (device-seen 2026-08-14).
  final int? id;

  /// The recognised source text (may still be partial).
  final String heard;

  /// BCP-47 code the model detected for [heard], when it reported one.
  final String? heardLang;

  /// The translation. Empty until it arrives — or forever, when
  /// [sameLanguage] is true.
  final String translated;

  /// True once the source utterance is finalised (the UI un-mutes it).
  final bool heardFinal;

  /// The speaker was ALREADY using the target language, so the model stays
  /// silent by design (`echoTargetLanguage: false`).
  ///
  /// This is the single most confusing thing about live translation — silence
  /// reads as a broken feature — so it is carried as state the screen can
  /// explain, not inferred from an empty string.
  final bool sameLanguage;

  /// Nothing more is coming for this turn.
  bool get isComplete => sameLanguage || (heardFinal && translated.isNotEmpty);

  TranslateTurn copyWith({
    String? heard,
    String? heardLang,
    String? translated,
    bool? heardFinal,
    bool? sameLanguage,
    int? id,
    String? targetLang,
  }) =>
      TranslateTurn(
        heard: heard ?? this.heard,
        heardLang: heardLang ?? this.heardLang,
        translated: translated ?? this.translated,
        heardFinal: heardFinal ?? this.heardFinal,
        sameLanguage: sameLanguage ?? this.sameLanguage,
        id: id ?? this.id,
        targetLang: targetLang ?? this.targetLang,
      );
}

/// Immutable snapshot the screen renders.
class TranslateState {
  const TranslateState({
    this.status = TranslateStatus.idle,
    this.targetLanguage = '',
    this.turns = const [],
    this.startedAt,
    this.captionsOnly = false,
    this.typing = false,
    this.mic = 'phone',
    this.error,
    this.notice,
  });

  final TranslateStatus status;

  /// The microphone this session listens with: `phone` or `glasses`. The
  /// translate screen's own choice — the assistant's is not touched by it.
  final String mic;

  /// A typed session: sentences are typed, not heard. Opened when the
  /// glasses are not connected — there is nowhere for a spoken translation
  /// to come out that the microphone cannot hear, so the microphone is
  /// never opened at all.
  final bool typing;

  /// BCP-47 code being translated into.
  final String targetLanguage;

  /// Oldest first. Capped by the controller.
  final List<TranslateTurn> turns;

  /// When the current session started, for the elapsed clock.
  final DateTime? startedAt;

  /// Text only — the translation is not spoken.
  final bool captionsOnly;

  /// A message to show the user. Not fatal on its own.
  final String? error;

  /// A non-alarming message: a quota heads-up, a device that dropped and was
  /// worked around. Rendered differently from [error] on purpose — colouring a
  /// "10 minutes left" warning like a failure teaches people to ignore both.
  final String? notice;

  bool get isRunning =>
      status == TranslateStatus.listening ||
      status == TranslateStatus.starting ||
      status == TranslateStatus.reconnecting;

  /// Elapsed time, derived from [startedAt] rather than counted — a ticking
  /// counter drifts and stops when the isolate is paused.
  Duration get elapsed => startedAt == null
      ? Duration.zero
      : DateTime.now().difference(startedAt!);

  String get elapsedLabel {
    final s = elapsed.inSeconds;
    final m = (s ~/ 60).toString();
    return '$m:${(s % 60).toString().padLeft(2, '0')}';
  }

  TranslateState copyWith({
    TranslateStatus? status,
    String? targetLanguage,
    List<TranslateTurn>? turns,
    DateTime? startedAt,
    bool clearStartedAt = false,
    bool? captionsOnly,
    bool? typing,
    String? mic,
    String? error,
    bool clearError = false,
    String? notice,
    bool clearNotice = false,
  }) =>
      TranslateState(
        status: status ?? this.status,
        targetLanguage: targetLanguage ?? this.targetLanguage,
        turns: turns ?? this.turns,
        startedAt: clearStartedAt ? null : (startedAt ?? this.startedAt),
        captionsOnly: captionsOnly ?? this.captionsOnly,
        typing: typing ?? this.typing,
        mic: mic ?? this.mic,
        error: clearError ? null : (error ?? this.error),
        notice: clearNotice ? null : (notice ?? this.notice),
      );
}
