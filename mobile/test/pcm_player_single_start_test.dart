/// One playback stream per player, however many chunks arrive at once.
///
/// The controller forwards every OUTPUT_AUDIO frame with `unawaited`, so
/// several `feed()` calls are in flight at the same time by design. `start()`
/// only set `_streaming = true` AFTER `startPlayerFromStream` returned, so each
/// of those concurrent feeds looked at a false flag and opened another stream
/// on the same engine. flutter_sound serialises the calls internally, so they
/// did not race into each other — they simply ran one after another, each one
/// tearing down the stream the last one had just built.
///
/// Device log, 2026-08-20: eight "playback stream started @ 24000Hz" lines
/// inside one second, and then no audio for the remaining eleven minutes of the
/// session. The assistant kept replying — the text arrived, the transcript
/// filled — but `PcmPlayer` never logged another start, because by then
/// `_streaming` was finally true and the engine underneath it was wedged.
/// Nothing pointed at the audio path: it looked like the glasses were silent.
library;

import 'dart:typed_data';

import 'package:farryon/playback/pcm_player.dart';
import 'package:flutter_sound/flutter_sound.dart';
import 'package:flutter_test/flutter_test.dart';

/// A stand-in engine whose open/start take a turn of the event loop — which is
/// the whole point: the window between "start was asked for" and "start
/// finished" is where the second caller used to slip in.
class _CountingEngine extends FlutterSoundPlayer {
  int opens = 0;
  int streamStarts = 0;
  int stops = 0;
  int feeds = 0;

  @override
  Future<FlutterSoundPlayer?> openPlayer({isBGService = false}) async {
    opens++;
    await Future<void>.delayed(const Duration(milliseconds: 5));
    return this;
  }

  @override
  Future<void> startPlayerFromStream({
    required Codec codec,
    required bool interleaved,
    required int numChannels,
    required int sampleRate,
    required int bufferSize,
    TWhenFinished? onBufferUnderlow,
  }) async {
    streamStarts++;
    await Future<void>.delayed(const Duration(milliseconds: 5));
  }

  @override
  Future<int> feedUint8FromStream(Uint8List buffer) async {
    feeds++;
    return buffer.length;
  }

  @override
  Future<void> stopPlayer() async => stops++;
}

void main() {
  late _CountingEngine engine;
  late PcmPlayer player;

  setUp(() {
    engine = _CountingEngine();
    player = PcmPlayer(player: engine);
  });

  Uint8List chunk() => Uint8List(960); // 20 ms of 24 kHz PCM16 mono

  test('eight chunks arriving at once open ONE stream, not eight', () async {
    // Exactly the shape of the real failure: the controller does not await
    // feed(), so the frames pile in while the first start is still opening.
    final feeds = List.generate(8, (_) => player.feed(chunk()));
    await Future.wait(feeds);

    expect(engine.streamStarts, 1,
        reason: 'the eight concurrent feeds must join one start');
    expect(engine.opens, 1, reason: 'and open the engine once');
    expect(engine.feeds, 8, reason: 'every chunk still reaches the speaker');
  });

  test('audio still plays after the burst — the engine is not wedged',
      () async {
    // The half that actually hurt. Losing the first sentence would have been
    // survivable; what happened instead was that playback never came back.
    await Future.wait(List.generate(8, (_) => player.feed(chunk())));
    final startsAfterBurst = engine.streamStarts;

    await player.feed(chunk());

    expect(engine.streamStarts, startsAfterBurst,
        reason: 'already streaming — no new stream needed');
    expect(engine.feeds, 9, reason: 'and the later audio is still fed');
  });

  test('a later turn re-opens the stream exactly once after a flush', () async {
    await player.feed(chunk());
    expect(engine.streamStarts, 1);

    await player.flush(); // barge-in: tears down and re-primes
    final afterFlush = engine.streamStarts;

    await Future.wait(List.generate(5, (_) => player.feed(chunk())));

    expect(engine.streamStarts, afterFlush,
        reason: 'flush already re-primed; the five feeds must not add more');
  });

  test('initialize() called concurrently opens the engine once', () async {
    await Future.wait([
      player.initialize(),
      player.initialize(),
      player.initialize(),
    ]);
    expect(engine.opens, 1);
  });
}
