import 'dart:async';
import 'dart:typed_data';

import 'package:flutter_sound/flutter_sound.dart';

import '../core/logger.dart';
import '../protocol/protocol.dart';

/// Low-latency streaming player for assistant TTS audio (OUTPUT_AUDIO, 0x03).
///
/// Fed raw **PCM16 LE mono 24 kHz** chunks as they arrive off the socket and
/// plays them with minimal buffering. Built on `flutter_sound`'s player in
/// *stream* mode (`startPlayerFromStream`), which accepts a `Stream<Uint8List>`
/// of raw PCM — the natural sink for our decoded binary frames and the same
/// engine the recorder uses on the capture side.
///
/// [flush] supports barge-in/interrupt: it drops any audio still queued so the
/// assistant goes quiet immediately when the user starts talking.
class PcmPlayer {
  /// [player] exists so a test can stand in for the platform engine and count
  /// how many times a stream is opened. Production passes nothing.
  PcmPlayer({FlutterSoundPlayer? player})
      : _player = player ?? FlutterSoundPlayer();

  static final _log = Logger('PcmPlayer');

  final FlutterSoundPlayer _player;
  bool _opened = false;
  bool _streaming = false;

  /// When the audio fed so far will have finished playing.
  ///
  /// The echo guard needs a lower bound on "the speaker is still talking" that
  /// CANNOT under-run — an early unmute lets the assistant's own voice back
  /// into the mic and it answers itself (device-proven 2026-08-05: the chat
  /// showed 'You: I'm not afraid to ask anything, no' seconds after Farry said
  /// 'feel free to ask me anything'). Every fed chunk pushes this deadline out
  /// by its own duration, starting from whichever is later: now, or the
  /// deadline already pending. So bursts, restarts, and multiple audio_start
  /// events per turn all accumulate correctly instead of resetting a guess.
  DateTime? _drainUntil;

  /// Bytes per second of playback: 24 kHz × 16-bit mono.
  static const int _bytesPerSecond = AudioFormat.ttsSampleRate * 2;

  /// True while fed audio should still be coming out of the speaker, plus
  /// [tail] for the speaker's decay and the room's ring-down.
  bool isPlayingWithin(Duration tail) {
    final until = _drainUntil;
    if (until == null) return false;
    return DateTime.now().isBefore(until.add(tail));
  }

  /// Set while [initialize] or [start] is in flight, so concurrent callers
  /// join the attempt already running instead of launching another one.
  ///
  /// Both flags guard the same shape of bug: the `_opened` / `_streaming`
  /// booleans are only true AFTER their await returns, so a second caller
  /// arriving during that await sees "not started yet" and starts again.
  /// Audio frames arrive off the socket every few tens of milliseconds and the
  /// controller forwards them with `unawaited`, so "a second caller during the
  /// await" is the normal case, not a rare one.
  Future<void>? _opening;
  Future<void>? _starting;

  /// Prepare the audio engine. Idempotent; call before [start].
  Future<void> initialize() {
    if (_opened) return Future<void>.value();
    return _opening ??= _open();
  }

  Future<void> _open() async {
    try {
      await _player.openPlayer();
      _opened = true;
      _log.debug('player opened');
    } finally {
      _opening = null;
    }
  }

  /// Begin a playback stream at [AudioFormat.ttsSampleRate] (24 kHz), mono.
  ///
  /// `interleaved: false` selects the low-overhead Float32/Int16 stream feeder
  /// path; we push Int16 PCM via [feed]. Safe to call repeatedly — a no-op if
  /// already streaming.
  Future<void> start() async {
    await initialize();
    if (_streaming) return;
    // Join an attempt already in flight rather than opening a second stream on
    // the same player — see [_starting]. Eight of those in one second is what
    // killed playback for the rest of the session (device log 2026-08-20).
    return _starting ??= _start();
  }

  Future<void> _start() async {
    try {
      await _startStream();
      _streaming = true;
      _log.info('playback stream started @ ${AudioFormat.ttsSampleRate}Hz');
    } finally {
      _starting = null;
    }
  }

  Future<void> _startStream() async {
    await _player.startPlayerFromStream(
      codec: Codec.pcm16,
      numChannels: AudioFormat.channels,
      sampleRate: AudioFormat.ttsSampleRate, // 24 kHz
      // 4096 bytes is 85 ms, which is fine for the phone's own speaker and far
      // too little for Bluetooth. On the glasses it starved the A2DP stream —
      // `btif_a2dp_source: UNDERFLOW: ONLY READ 0 BYTES OUT OF 4096`, then
      // `ack_stream_suspended` — so the translation arrived chopped up and the
      // route sometimes fell back to the phone's loudspeaker mid-sentence
      // (device log, 2026-08-13). A Bluetooth link needs a few hundred
      // milliseconds of slack; this is ~0.7 s, still short enough that
      // interrupting the assistant feels immediate.
      bufferSize: 32768,
      interleaved: true,
    );
  }

  /// Feed one chunk of PCM16 LE mono 24 kHz audio for playback.
  ///
  /// Lazily starts the stream if needed so callers can simply forward decoded
  /// OUTPUT_AUDIO payloads without sequencing [start] themselves.
  Future<void> feed(Uint8List pcm16) async {
    if (pcm16.isEmpty) return;
    // Extend the drain deadline BEFORE awaiting the feed: the guard must know
    // this audio is coming even while backpressure holds the write.
    final now = DateTime.now();
    final base =
        (_drainUntil != null && _drainUntil!.isAfter(now)) ? _drainUntil! : now;
    _drainUntil = base.add(
      Duration(microseconds: pcm16.length * 1000000 ~/ _bytesPerSecond),
    );
    if (!_streaming) await start();
    // `feedUint8FromStream` applies backpressure internally; awaiting it keeps
    // memory bounded if the network outruns the speaker.
    await _player.feedUint8FromStream(pcm16);
  }

  /// Drop all queued/playing audio immediately (barge-in / interrupt).
  ///
  /// Tears the stream down and re-arms a fresh one so the *next* [feed] starts
  /// clean. This is what the controller calls alongside sending `interrupt`.
  Future<void> flush() async {
    if (!_opened) return;
    _log.info('flush (barge-in)');
    _drainUntil = null; // queued audio is dropped — nothing left to play out
    if (_streaming) {
      try {
        await _player.stopPlayer();
      } catch (e) {
        _log.warn('stopPlayer during flush failed: $e');
      }
      _streaming = false;
    }
    // Re-prime so subsequent assistant audio plays without an extra await on
    // the caller's hot path.
    await start();
  }

  /// Stop playback entirely (e.g. session ended). Keeps the engine open so it
  /// can be [start]ed again cheaply.
  Future<void> stop() async {
    _drainUntil = null;
    if (_streaming) {
      try {
        await _player.stopPlayer();
      } catch (e) {
        _log.warn('stopPlayer failed: $e');
      }
      _streaming = false;
    }
  }

  /// Release the audio engine. The player is unusable afterwards.
  Future<void> dispose() async {
    await stop();
    if (_opened) {
      await _player.closePlayer();
      _opened = false;
    }
  }
}
