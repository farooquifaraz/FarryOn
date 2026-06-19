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
  PcmPlayer();

  static final _log = Logger('PcmPlayer');

  final FlutterSoundPlayer _player = FlutterSoundPlayer();
  bool _opened = false;
  bool _streaming = false;

  /// Prepare the audio engine. Idempotent; call before [start].
  Future<void> initialize() async {
    if (_opened) return;
    await _player.openPlayer();
    _opened = true;
    _log.debug('player opened');
  }

  /// Begin a playback stream at [AudioFormat.ttsSampleRate] (24 kHz), mono.
  ///
  /// `interleaved: false` selects the low-overhead Float32/Int16 stream feeder
  /// path; we push Int16 PCM via [feed]. Safe to call repeatedly — a no-op if
  /// already streaming.
  Future<void> start() async {
    await initialize();
    if (_streaming) return;
    await _player.startPlayerFromStream(
      codec: Codec.pcm16,
      numChannels: AudioFormat.channels,
      sampleRate: AudioFormat.ttsSampleRate, // 24 kHz
      // Keep internal buffering small for low latency; flutter_sound feeds the
      // OS audio unit as chunks arrive.
      bufferSize: 4096,
      interleaved: true,
    );
    _streaming = true;
    _log.info('playback stream started @ ${AudioFormat.ttsSampleRate}Hz');
  }

  /// Feed one chunk of PCM16 LE mono 24 kHz audio for playback.
  ///
  /// Lazily starts the stream if needed so callers can simply forward decoded
  /// OUTPUT_AUDIO payloads without sequencing [start] themselves.
  Future<void> feed(Uint8List pcm16) async {
    if (pcm16.isEmpty) return;
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
