import 'dart:collection';
import 'dart:math' as math;
import 'dart:typed_data';

/// Energy gate for the outgoing mic stream: send speech, hold back silence.
///
/// Why this exists. The phone streams the microphone continuously, so the
/// model's own turn detector also hears every quiet room — a fan, a distant
/// TV, a door. It occasionally scores that as speech, and the transcriber then
/// *invents* words for it: the user's chat filled with lines they never said
/// ("Bye. It's smoke.", "Open harbor.", device-proven 2026-08-05) and the
/// assistant answered each one. No text-side filter can undo that reliably —
/// the fix is to not send non-speech in the first place.
///
/// The gate is deliberately biased toward sending:
///   * the threshold floats above the *measured* room noise, so a quiet room
///     lowers it and a noisy one raises it, instead of a fixed guess;
///   * [preRoll] of audio is kept buffered and flushed the moment speech
///     starts, so word onsets are never clipped;
///   * [hangover] keeps the stream open after speech stops — long enough for
///     the server's own VAD to hear the trailing silence it needs to close the
///     turn (its silence window is 700 ms).
///
/// Provider-independent: it sits on the raw PCM before any gateway, so OpenAI,
/// Gemini and anything added later benefit equally.
class MicGate {
  MicGate({
    this.sampleRate = 16000,
    this.preRoll = const Duration(milliseconds: 400),
    this.hangover = const Duration(seconds: 1),
    this.noiseMultiplier = 2.2,
    this.absoluteFloor = 180.0,
    this.maxNoiseFloor = 450.0,
    this.onsetMs = 0,
    this.floorFromWindow = false,
    this.floorWindow = const Duration(seconds: 8),
    this.keepOpenRatio = 1.0,
    this.speakerBar = false,
    this.speakerBarMin = 0,
    this.fastOpenRatio = 0,
    this.fastOpenMin = 0,
    DateTime Function()? clock,
  }) : _now = clock ?? DateTime.now;

  /// The profile for the glasses' call-mode (SCO) microphone.
  ///
  /// That signal is hot and omnidirectional: the wearer's voice measured
  /// 700–11000 RMS where the phone mic gives 80–600, and the room — a TV,
  /// someone talking behind the wearer — lands well above the phone
  /// profile's bar (450 × 2.2 = 990), so every voice in the room opened the
  /// gate and became a turn (live 2026-09-13: empty turns, mixed
  /// transcripts). The floor may learn higher, the bar sits further above
  /// it, and speech must HOLD for [onsetMs] before the gate opens — a word
  /// fragment from across the room does not. The pre-roll keeps the onset.
  ///
  /// Measured on the audio the gate let through (2026-09-13 19:03-19:12,
  /// L802 call-mode mic, TV on): the TV alone reached p90 3,300 / p98 6,800
  /// RMS; the wearer's own speech sat at p80 8,200 / p90 11,200 / p98
  /// 16,700. Replayed against that TV recording, a bar of 6,000 (floor
  /// 2,400 x 2.5) held for 240 ms let ZERO TV bursts through while keeping
  /// 12 of the 14 voice openings of the speech recording (the two lost were
  /// sub-240 ms grunts); 5,000 / 160 ms still let one TV burst per 40 s in.
  /// The window may raise the floor to 2,800 (bar 7,000) for a louder room,
  /// never higher, so a raised voice always gets through.
  ///
  /// Two things that bar got wrong, measured on the wearer's own sentences
  /// two days later (2026-09-15 09:57, three utterances the gate DID pass):
  /// only 32-42 % of their chunks were over 6,000 — the median of his
  /// speech was ~5,000, UNDER the bar — and the ends of sentences sat at
  /// 3,200-3,400. So a 6,000 bar held for the whole utterance closed the
  /// gate mid-sentence on any 900 ms stretch of ordinary speech (one
  /// utterance had 1,080 ms of it) and cut every sentence's tail; and
  /// relaxed speech, whose stressed syllables alone crossed the bar (misses
  /// logged with peaks of 8,500-11,800 but only 120-360 ms over it), never
  /// opened it at all — "deaf after a few minutes", on both servers.
  ///
  /// Hence: the bar opens the gate; a much lower one KEEPS it open
  /// ([keepOpenRatio] 0.4 → 2,400, under which those utterances never
  /// stayed for more than 440 ms); and the opening bar follows the
  /// wearer's own measured speech level ([speakerBar], never below
  /// [speakerBarMin] 4,000 — above the TV's p90 of 3,300 — nor above the
  /// room-derived bar). A room's bar (floor × 2.5, capped at 7,000) still
  /// wins in a loud room.
  factory MicGate.glasses({DateTime Function()? clock}) => MicGate(
        absoluteFloor: 2400.0,
        maxNoiseFloor: 2800.0,
        noiseMultiplier: 2.5,
        onsetMs: 200,
        hangover: const Duration(milliseconds: 900),
        floorFromWindow: true,
        keepOpenRatio: 0.4,
        speakerBar: true,
        speakerBarMin: 4000.0,
        fastOpenRatio: 2.0,
        fastOpenMin: 8000.0,
        clock: clock,
      );

  /// A single chunk this far over the opening bar (and over [fastOpenMin])
  /// opens the gate at once, no onset needed. Device 2026-09-15 14:29-14:31:
  /// five short "Hello"s were held back with peaks of 10,000-18,000 — two
  /// to three times the bar — because they were loud for 120-200 ms and the
  /// onset wanted 240. Nothing in the room comes close: the TV's p98 was
  /// 6,800. Zero = off (the phone profile).
  final double fastOpenRatio;
  final double fastOpenMin;

  /// Once open, the level that counts as "still speaking" is this fraction
  /// of the opening bar (1.0 = the same bar, the phone behaviour). Speech
  /// is loud on its stressed syllables and quiet between and after them;
  /// holding the gate to the opening bar throughout cut sentences in the
  /// middle and always at the end (see [MicGate.glasses]).
  final double keepOpenRatio;

  /// Learn the wearer's own speech level from the utterances the gate
  /// passed, and let the opening bar follow it (0.9 × the running median
  /// of speech-loud chunks, never under [speakerBarMin], never over the
  /// room's bar). Someone who talks softly after the first minute is still
  /// heard; a TV that never reaches [speakerBarMin] still is not.
  final bool speakerBar;
  final double speakerBarMin;

  final int sampleRate;

  /// Speech-loud audio must add up to this much before the gate opens (0 =
  /// open on the first loud chunk, the phone behaviour). The chunks that
  /// make up the onset are kept and flushed with the pre-roll, so nothing
  /// is lost.
  ///
  /// Added up within [_onsetWindow], not counted consecutively: speech dips
  /// between syllables and on plosives, and one 40 ms chunk under the bar
  /// used to reset the count to zero — a soft or short "Hello." with one
  /// dip in its first quarter second never opened the gate while the chip
  /// went on saying Listening (Faraz, 2026-09-15 00:25). A burst from the
  /// room still has to be loud for [onsetMs] out of the window; it only
  /// gets to pause for the difference.
  final int onsetMs;

  /// The span the onset may be spread over (onset + up to 160 ms of dips).
  Duration get _onsetWindow => Duration(milliseconds: onsetMs + 160);

  /// Learn the room from the quiet fifth of the last [floorWindow] of audio,
  /// whether the gate is open or not — instead of only while it is closed.
  ///
  /// The closed-only rule cannot learn a room that never goes quiet: with a
  /// TV on, the glasses gate opened on the very first chunk and from then on
  /// never closed long enough to learn, so the floor sat at its minimum and
  /// the TV made a "turn" every couple of seconds (device-seen 2026-09-13
  /// 19:03: 14 empty turns in a minute, Thai and "<noise>" transcripts). A
  /// low percentile of a sliding window sees through both speech and TV
  /// dialogue — each has gaps — to whatever is steady underneath, so the bar
  /// climbs above a continuous background and a voice on the face still
  /// clears it. Off for the phone profile, whose behaviour is unchanged.
  final bool floorFromWindow;
  final Duration floorWindow;

  /// With [floorFromWindow], the floor is frozen while the gate is open for
  /// less than this — an utterance must not teach the gate that the wearer's
  /// own voice is the room and then chop the end of a long sentence. A gate
  /// held open longer than this is hearing a background, not a sentence, and
  /// learning resumes.
  static const Duration _longOpen = Duration(seconds: 15);

  /// The window needs this many chunks before its percentile means anything;
  /// until then the floor stays where it is (the first chunks of a stream
  /// are as likely to be speech as room).
  static const int _minWindowChunks = 50;

  /// Audio kept before speech is detected, flushed with the first speech chunk.
  final Duration preRoll;

  /// How long the stream stays open after the last speech-loud chunk.
  final Duration hangover;

  /// Speech must be this many times louder than the measured noise floor.
  final double noiseMultiplier;

  /// Absolute RMS (PCM16 units) below which nothing counts as speech, however
  /// quiet the room gets — stops a silent room from making the gate jumpy.
  final double absoluteFloor;

  /// The most the measured "room" is ever allowed to be, in RMS units.
  ///
  /// The floor learns whatever is steady, and music from the phone's own
  /// speaker is steady: at a floor of 1000 the bar sits at 2200, and speech —
  /// which on this pipeline opens the gate at 180–500 most of the time (every
  /// gate-open logged up to 2026-09-08) — never clears it again. The user
  /// talks over the music and nothing is sent, which is exactly "Farry can't
  /// hear me while music plays". Capping the floor bounds the harm: loud
  /// music may then pass the gate as if it were speech (the server's own
  /// turn detector still judges it), but a voice raised over it can always
  /// get through. 450 → a bar of 990, above every quiet-room opening seen and
  /// below the levels a person reaches when they raise their voice.
  final double maxNoiseFloor;

  final DateTime Function() _now;

  final Queue<Uint8List> _ring = Queue<Uint8List>();
  int _ringBytes = 0;

  /// Starts at [absoluteFloor] rather than "whatever arrives first", then
  /// settles onto the real room level (see the update rule in [process]).
  late double _noiseFloor = absoluteFloor;
  bool _open = false;
  DateTime? _lastSpeechAt;

  /// Speech-loud chunks seen while closed, with arrival times, so the onset
  /// can be added up over [_onsetWindow] (see [onsetMs]).
  final Queue<(DateTime, int)> _loud = Queue<(DateTime, int)>();

  // A stretch of speech-LIKE audio (above half the bar) that did not open
  // the gate — measured so "she can't hear me" comes with numbers instead
  // of a guess: how loud it got, how long it stayed loud, and the bar it
  // was measured against. See [onMiss].
  DateTime? _missStart;
  DateTime? _missLastLoud;
  double _missPeak = 0;
  int _missLoudBytes = 0;
  int _missHalfBytes = 0;

  /// Recent chunk levels for [floorFromWindow]: (arrival, rms).
  final Queue<(DateTime, double)> _levels = Queue<(DateTime, double)>();
  DateTime? _openSince;

  /// Called each time the gate opens, with the chunk's level and the bar it
  /// had to clear. If a user ever reports "she can't hear me", this is the
  /// number that says whether their voice reached the bar — without it the
  /// gate would be an invisible place for speech to disappear.
  void Function(double rms, double threshold)? onOpen;

  /// Called each time the gate closes — the hangover after the last loud
  /// chunk elapsed, or [reset] shut an open gate. Paired with [onOpen] it
  /// brackets one utterance (the backend's manual activity window).
  void Function()? onClose;

  /// Called when a stretch of speech-like audio (at least [_missMinMs]
  /// above half the bar) ended without the gate opening: its peak level,
  /// the bar it faced, how long it was actually over the bar, and how long
  /// it was over half of it. The number that says whether the wearer's
  /// voice reached the bar — or fell short, and by how much.
  void Function(double peakRms, double threshold, int loudMs, int halfMs)?
      onMiss;

  /// A candidate has to be speech-like for this long to be worth reporting;
  /// shorter blips are the room.
  static const int _missMinMs = 200;

  /// The candidate ends after this much audio under half the bar.
  static const Duration _missGap = Duration(milliseconds: 400);

  /// Level and threshold at the most recent open. Diagnostics.
  double lastOpenRms = 0;
  double lastOpenThreshold = 0;

  /// Smoothed level of everything the gate has seen lately (~2 s), open or
  /// closed. A miss with a loud peak but a low mean is a chopped stream, not
  /// a quiet voice.
  double get recentMeanRms => _meanRms;
  double _meanRms = 0;

  /// True while the gate is passing audio (speech + hangover). Diagnostics.
  bool get isOpen => _open;

  /// Extra factor on the bar while something steady and loud that is NOT
  /// the user is playing (music). 1.0 = off.
  ///
  /// On the glasses the music goes out over the same SCO link the mic comes
  /// in on, with no echo canceller in between, so the mic hears the song at
  /// speech level and the gate opened once a second (device 2026-09-13
  /// 14:56). The floor cap keeps the bar from following the music up; this
  /// lifts it deliberately while music is on, low enough that a voice
  /// raised over the song still clears it.
  double musicBoost = 1.0;

  /// Measured background level in PCM16 RMS units. Diagnostics.
  double get noiseFloor => _noiseFloor;

  /// The wearer's measured speech level (median of speech-loud chunks
  /// while the gate was open), or 0 before anything was heard.
  double get speakerLevel => _speakerLevel;
  double _speakerLevel = 0;

  /// Levels of the last few hundred speech-loud chunks while open, for the
  /// running median that [speakerLevel] is.
  final List<double> _speechLevels = <double>[];
  static const int _speechLevelsMax = 300;

  int get _bytesPerSecond => sampleRate * 2;

  /// The bar a chunk must clear right now to count as speech. Exposed so the
  /// caller can measure audio it is about to DROP against the same bar the
  /// gate would have used, instead of a second guess of its own.
  double get threshold => _openBar();

  /// The bar the gate opens on.
  ///
  /// Without a learned wearer: the room's bar as it always was —
  /// max(absoluteFloor, floor × multiplier), with the floor clamped to
  /// [absoluteFloor, maxNoiseFloor]. With one: the greater of the wearer's
  /// own bar (0.9 × their speech median, never under [speakerBarMin]) and
  /// what the room ACTUALLY measures (its unclamped quiet fifth ×
  /// multiplier) — so a quiet room lets the bar come down to the wearer,
  /// and a room with a TV in it (quiet fifth 2,800+) keeps it at 7,000 as
  /// before. Never above the room's capped bar, whoever is talking.
  double _openBar() {
    final room = math.max(absoluteFloor, _noiseFloor * noiseMultiplier);
    double bar = room;
    if (speakerBar && _speakerLevel > 0) {
      final own = math.max(speakerBarMin, _speakerLevel * 0.9);
      final measured = _rawFloor * noiseMultiplier;
      bar = math.min(math.max(own, measured), maxNoiseFloor * noiseMultiplier);
    }
    return bar * musicBoost;
  }

  /// The room's quiet fifth as measured, before the [absoluteFloor] clamp
  /// that keeps the legacy bar at 6,000 in a silent room.
  double _rawFloor = 0;

  /// The level under which an OPEN gate starts counting silence (for the
  /// first [_longOpen] of an utterance; see [process]).
  double get keepOpenThreshold => _openBar() * keepOpenRatio;

  /// RMS level of a chunk in PCM16 units; 0 for anything unmeasurable.
  double levelOf(Uint8List pcm16) {
    try {
      return _rms(pcm16);
    } catch (_) {
      return 0;
    }
  }

  /// Feed one captured chunk; returns the chunks to actually transmit.
  ///
  /// Empty means "hold this back". When speech starts the result contains the
  /// buffered pre-roll followed by [pcm16].
  List<Uint8List> process(Uint8List pcm16) {
    if (pcm16.length < 2) return const [];
    final double rms;
    try {
      rms = _rms(pcm16);
    } catch (_) {
      // FAIL OPEN, always. The gate must never be the reason a user's voice
      // disappears: anything it cannot measure, it forwards. (It shipped
      // failing CLOSED once — a RangeError on the first chunk killed the whole
      // mic stream and Farry went deaf, 2026-08-05.)
      return [pcm16];
    }
    final now = _now();
    _meanRms = _meanRms == 0 ? rms : _meanRms * 0.96 + rms * 0.04;

    // Learn the room only while we're NOT passing speech, so the speaker's own
    // voice and the user's can't inflate the floor and deafen the gate.
    //
    // Asymmetric on purpose: quiet evidence is trusted quickly, loud evidence
    // slowly. Letting a single loud chunk set the floor meant that speaking
    // the instant a session opened taught the gate that YOUR VOICE was the
    // room, and it then held back everything quieter — caught by the
    // controller test, which speaks on its very first chunk.
    if (floorFromWindow) {
      _levels.addLast((now, rms));
      while (_levels.isNotEmpty &&
          now.difference(_levels.first.$1) > floorWindow) {
        _levels.removeFirst();
      }
      final openFor = _openSince == null ? Duration.zero : now.difference(_openSince!);
      final mayLearn = !_open || openFor > _longOpen;
      if (mayLearn && _levels.length >= _minWindowChunks) {
        final sorted = _levels.map((e) => e.$2).toList()..sort();
        final p20 =
            sorted[(sorted.length * 0.2).floor().clamp(0, sorted.length - 1)];
        _rawFloor = p20;
        _noiseFloor = p20.clamp(absoluteFloor, maxNoiseFloor);
      }
    } else if (!_open) {
      _noiseFloor = rms < _noiseFloor
          ? (_noiseFloor * 0.9) + (rms * 0.1)
          : (_noiseFloor * 0.995) + (rms * 0.005);
      if (_noiseFloor > maxNoiseFloor) _noiseFloor = maxNoiseFloor;
    }
    final threshold = _openBar();

    if (!_open) _trackMiss(now, rms, threshold, pcm16.length);

    // What the wearer sounds like: every chunk of an open utterance that
    // is speech (over the keep bar), stressed and unstressed alike.
    if (_open && speakerBar && rms > threshold * keepOpenRatio) {
      _learnSpeaker(rms);
    }

    // Open: the wearer keeps the gate with the lower bar — stressed
    // syllables clear the opening bar, the words between them do not. A
    // gate open longer than [_longOpen] is not hearing a sentence but a
    // background that once cleared the bar (a TV); from there on it takes
    // the full bar to stay open, so the room's bar can shut it as before.
    final openFor =
        _openSince == null ? Duration.zero : now.difference(_openSince!);
    final keepBar =
        openFor > _longOpen ? threshold : threshold * keepOpenRatio;
    final speaking = _open ? rms > keepBar : rms > threshold;
    if (speaking) {
      _lastSpeechAt = now;
      if (!_open) {
        final need = onsetMs * _bytesPerSecond ~/ 1000;
        final unmistakable = fastOpenRatio > 0 &&
            rms > math.max(threshold * fastOpenRatio, fastOpenMin);
        if (need > 0 && !unmistakable) {
          _loud.addLast((now, pcm16.length));
          while (_loud.isNotEmpty &&
              now.difference(_loud.first.$1) > _onsetWindow) {
            _loud.removeFirst();
          }
          var loudBytes = 0;
          for (final e in _loud) {
            loudBytes += e.$2;
          }
          if (loudBytes < need) {
            // Loud, but not for long enough yet: hold it with the pre-roll.
            _remember(pcm16);
            return const [];
          }
        }
        _loud.clear();
        _clearMiss();
        _open = true;
        _openSince = now;
        lastOpenRms = rms;
        lastOpenThreshold = threshold;
        onOpen?.call(rms, threshold);
        final flush = <Uint8List>[..._ring, pcm16];
        _ring.clear();
        _ringBytes = 0;
        return flush;
      }
      return [pcm16];
    }

    // Below threshold: keep streaming through the hangover so the tail of a
    // word — and the silence the server needs to end the turn — still gets out.
    final last = _lastSpeechAt;
    if (_open && last != null && now.difference(last) < hangover) {
      return [pcm16];
    }
    if (_open) {
      _open = false;
      _openSince = null;
      onClose?.call();
    }
    _remember(pcm16);
    return const [];
  }

  void _learnSpeaker(double rms) {
    _speechLevels.add(rms);
    if (_speechLevels.length > _speechLevelsMax) _speechLevels.removeAt(0);
    if (_speechLevels.length < 12) return; // half a second of speech
    final sorted = List<double>.of(_speechLevels)..sort();
    _speakerLevel = sorted[sorted.length ~/ 2];
  }

  /// Track a closed-gate stretch of speech-like audio; report it when it
  /// ends without an opening. Cheap: a few fields per chunk.
  void _trackMiss(DateTime now, double rms, double threshold, int bytes) {
    final half = threshold / 2;
    if (rms > half) {
      _missStart ??= now;
      _missLastLoud = now;
      _missHalfBytes += bytes;
      if (rms > threshold) _missLoudBytes += bytes;
      if (rms > _missPeak) _missPeak = rms;
      return;
    }
    final lastLoud = _missLastLoud;
    if (_missStart == null || lastLoud == null) return;
    if (now.difference(lastLoud) < _missGap) return;
    final halfMs = _missHalfBytes * 1000 ~/ _bytesPerSecond;
    if (halfMs >= _missMinMs) {
      onMiss?.call(
        _missPeak,
        threshold,
        _missLoudBytes * 1000 ~/ _bytesPerSecond,
        halfMs,
      );
    }
    _clearMiss();
  }

  void _clearMiss() {
    _missStart = null;
    _missLastLoud = null;
    _missPeak = 0;
    _missLoudBytes = 0;
    _missHalfBytes = 0;
  }

  /// Forget buffered audio and reset the gate (mic closed, session ended).
  void reset() {
    _ring.clear();
    _ringBytes = 0;
    _loud.clear();
    _clearMiss();
    _levels.clear();
    _openSince = null;
    final wasOpen = _open;
    _open = false;
    _lastSpeechAt = null;
    _noiseFloor = absoluteFloor;
    _rawFloor = 0;
    // The wearer is the same person after a reset; what was learned about
    // their voice stays (only the ROOM is re-learned).
    // An utterance that was cut off (the speaker started) still ends: the
    // backend's manual activity window must not stay open.
    if (wasOpen) onClose?.call();
  }

  /// Forget the learned room level only — keep the pre-roll and the open
  /// state. For the moment something loud and steady (music) stops: the
  /// floor it taught is wrong now, and re-learning from silence is quicker
  /// than decaying from the old level.
  void resetFloor() {
    _noiseFloor = absoluteFloor;
    _rawFloor = 0;
    _levels.clear();
  }

  void _remember(Uint8List pcm16) {
    _ring.addLast(pcm16);
    _ringBytes += pcm16.length;
    final maxBytes = preRoll.inMilliseconds * _bytesPerSecond ~/ 1000;
    while (_ringBytes > maxBytes && _ring.isNotEmpty) {
      _ringBytes -= _ring.removeFirst().length;
    }
  }

  /// RMS of little-endian PCM16, read byte by byte.
  ///
  /// Deliberately NOT via `buffer.asInt16List`: the capture layer hands out
  /// views into a shared buffer whose offset can be odd, and that throws
  /// ("Offset must be a multiple of BYTES_PER_ELEMENT") — which is exactly how
  /// this gate once killed the entire mic stream. Index arithmetic on the view
  /// is alignment-agnostic and costs nothing at these sizes.
  double _rms(Uint8List bytes) {
    final count = bytes.length ~/ 2;
    if (count == 0) return 0;
    var sum = 0.0;
    for (var i = 0; i < count; i++) {
      var v = bytes[i * 2] | (bytes[i * 2 + 1] << 8);
      if (v >= 0x8000) v -= 0x10000; // two's complement
      sum += v * v.toDouble();
    }
    return math.sqrt(sum / count);
  }
}
