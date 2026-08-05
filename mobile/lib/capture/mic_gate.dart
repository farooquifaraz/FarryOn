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
    DateTime Function()? clock,
  }) : _now = clock ?? DateTime.now;

  final int sampleRate;

  /// Audio kept before speech is detected, flushed with the first speech chunk.
  final Duration preRoll;

  /// How long the stream stays open after the last speech-loud chunk.
  final Duration hangover;

  /// Speech must be this many times louder than the measured noise floor.
  final double noiseMultiplier;

  /// Absolute RMS (PCM16 units) below which nothing counts as speech, however
  /// quiet the room gets — stops a silent room from making the gate jumpy.
  final double absoluteFloor;

  final DateTime Function() _now;

  final Queue<Uint8List> _ring = Queue<Uint8List>();
  int _ringBytes = 0;

  /// Starts at [absoluteFloor] rather than "whatever arrives first", then
  /// settles onto the real room level (see the update rule in [process]).
  late double _noiseFloor = absoluteFloor;
  bool _open = false;
  DateTime? _lastSpeechAt;

  /// Called each time the gate opens, with the chunk's level and the bar it
  /// had to clear. If a user ever reports "she can't hear me", this is the
  /// number that says whether their voice reached the bar — without it the
  /// gate would be an invisible place for speech to disappear.
  void Function(double rms, double threshold)? onOpen;

  /// Level and threshold at the most recent open. Diagnostics.
  double lastOpenRms = 0;
  double lastOpenThreshold = 0;

  /// True while the gate is passing audio (speech + hangover). Diagnostics.
  bool get isOpen => _open;

  /// Measured background level in PCM16 RMS units. Diagnostics.
  double get noiseFloor => _noiseFloor;

  int get _bytesPerSecond => sampleRate * 2;

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

    // Learn the room only while we're NOT passing speech, so the speaker's own
    // voice and the user's can't inflate the floor and deafen the gate.
    //
    // Asymmetric on purpose: quiet evidence is trusted quickly, loud evidence
    // slowly. Letting a single loud chunk set the floor meant that speaking
    // the instant a session opened taught the gate that YOUR VOICE was the
    // room, and it then held back everything quieter — caught by the
    // controller test, which speaks on its very first chunk.
    if (!_open) {
      _noiseFloor = rms < _noiseFloor
          ? (_noiseFloor * 0.9) + (rms * 0.1)
          : (_noiseFloor * 0.995) + (rms * 0.005);
    }
    final threshold = math.max(absoluteFloor, _noiseFloor * noiseMultiplier);

    if (rms > threshold) {
      _lastSpeechAt = now;
      if (!_open) {
        _open = true;
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
    _open = false;
    _remember(pcm16);
    return const [];
  }

  /// Forget buffered audio and reset the gate (mic closed, session ended).
  void reset() {
    _ring.clear();
    _ringBytes = 0;
    _open = false;
    _lastSpeechAt = null;
    _noiseFloor = absoluteFloor;
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
