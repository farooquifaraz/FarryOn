import 'dart:math' as math;
import 'dart:typed_data';

import 'package:farryon/capture/mic_gate.dart';
import 'package:flutter_test/flutter_test.dart';

/// One 20 ms chunk of 16 kHz mono PCM16 at the given amplitude.
Uint8List chunk(double amplitude, {int ms = 20}) {
  final samples = 16000 * ms ~/ 1000;
  final data = Int16List(samples);
  for (var i = 0; i < samples; i++) {
    // A tone, so RMS ≈ amplitude / sqrt(2) — shape doesn't matter, level does.
    data[i] = (amplitude * math.sin(i * 0.3)).round();
  }
  return data.buffer.asUint8List();
}

void main() {
  test('levelOf and threshold expose what the gate itself would decide', () {
    final gate = MicGate();
    // The bar starts at the absolute floor times the multiplier and a loud
    // chunk clears it; a faint one does not — the same answer process()
    // gives, so audio measured at the point of discard is judged fairly.
    expect(gate.threshold, gate.absoluteFloor * gate.noiseMultiplier);
    expect(gate.levelOf(chunk(2000)) > gate.threshold, isTrue);
    expect(gate.levelOf(chunk(40)) > gate.threshold, isFalse);
    expect(gate.levelOf(Uint8List(1)), 0, reason: 'unmeasurable is 0, never a throw');
  });

  test('quiet room is held back entirely', () {
    final gate = MicGate();
    var sent = 0;
    for (var i = 0; i < 100; i++) {
      sent += gate.process(chunk(40)).length; // faint background hiss
    }
    expect(sent, 0, reason: 'silence must never reach the model');
    expect(gate.isOpen, isFalse);
  });

  test('speech opens the gate and carries the pre-roll with it', () {
    final gate = MicGate();
    for (var i = 0; i < 50; i++) {
      gate.process(chunk(40)); // settle the noise floor, fills the ring
    }
    final out = gate.process(chunk(6000)); // someone speaks
    expect(out.length, greaterThan(1),
        reason: 'buffered pre-roll must precede the first speech chunk');
    expect(gate.isOpen, isTrue);
  });

  test('the tail of a sentence still gets through (hangover)', () {
    final gate = MicGate();
    for (var i = 0; i < 50; i++) {
      gate.process(chunk(40));
    }
    gate.process(chunk(6000));
    // Quiet again, but within the hangover: keep streaming so the word ending
    // and the silence the server needs to close the turn both arrive.
    expect(gate.process(chunk(40)), isNotEmpty);
    expect(gate.process(chunk(40)), isNotEmpty);
  });

  test('gate closes once the hangover expires', () {
    var now = DateTime(2026);
    final gate = MicGate(clock: () => now);
    for (var i = 0; i < 50; i++) {
      gate.process(chunk(40));
    }
    gate.process(chunk(6000));
    now = now.add(const Duration(seconds: 2)); // well past the hangover
    expect(gate.process(chunk(40)), isEmpty);
    expect(gate.isOpen, isFalse);
  });

  test('a noisy room raises the bar instead of leaking', () {
    final gate = MicGate();
    // Steady loud-ish background (traffic through a window).
    for (var i = 0; i < 200; i++) {
      gate.process(chunk(500));
    }
    expect(gate.isOpen, isFalse, reason: 'background alone must not open it');
    // Speech still gets through in that same room.
    expect(gate.process(chunk(8000)), isNotEmpty);
  });

  test('music cannot raise the bar out of reach', () {
    // Steady, loud music from the phone's own speaker. Before the cap the
    // floor followed it up and the bar went past anything a voice reaches
    // (every quiet-room gate-open logged sat at 180-500).
    final gate = MicGate();
    for (var i = 0; i < 600; i++) {
      gate.process(chunk(3000));
    }
    expect(gate.noiseFloor, lessThanOrEqualTo(gate.maxNoiseFloor));
    expect(gate.threshold, lessThanOrEqualTo(gate.maxNoiseFloor * gate.noiseMultiplier));
    // A voice raised over the music still gets through.
    expect(gate.process(chunk(1500)), isNotEmpty);
  });

  test('resetFloor forgets the room but keeps the pre-roll', () {
    final gate = MicGate();
    for (var i = 0; i < 50; i++) {
      gate.process(chunk(400));
    }
    expect(gate.noiseFloor, greaterThan(gate.absoluteFloor));
    gate.resetFloor();
    expect(gate.noiseFloor, gate.absoluteFloor);
    // The buffered pre-roll is still there: the first loud chunk carries it.
    expect(gate.process(chunk(8000)).length, greaterThan(1));
  });

  test('reset re-learns the room', () {
    final gate = MicGate();
    for (var i = 0; i < 50; i++) {
      gate.process(chunk(400));
    }
    final learned = gate.noiseFloor;
    gate.reset();
    expect(gate.noiseFloor, isNot(learned),
        reason: 'reset drops the learned room level back to the default');
    expect(gate.isOpen, isFalse);
  });

  test('chunks that are views into a shared buffer still work', () {
    // The capture layer hands out slices of an internal buffer, and the offset
    // can be ODD. Reading those through asInt16List threw a RangeError on the
    // very first chunk, which cancelled the mic subscription — Farry went
    // completely deaf until the app restarted (device-proven 2026-08-05).
    final backing = Uint8List(6401);
    final loud = chunk(6000);
    backing.setRange(1, 1 + loud.length, loud);
    final odd = Uint8List.view(backing.buffer, 1, loud.length);

    final gate = MicGate();
    expect(() => gate.process(odd), returnsNormally);
    expect(gate.process(chunk(6000)), isNotEmpty,
        reason: 'the gate must still hear speech after an odd-offset chunk');
  });

  test('an unmeasurable chunk is forwarded, never swallowed', () {
    // Fail OPEN: whatever the gate cannot judge, the model still gets. A gate
    // that fails closed is indistinguishable from a broken microphone.
    final gate = MicGate();
    final weird = Uint8List.fromList([1, 2, 3]); // odd byte count
    expect(gate.process(weird), isNotEmpty);
  });

  test('empty or malformed chunks are ignored safely', () {
    final gate = MicGate();
    expect(gate.process(Uint8List(0)), isEmpty);
    expect(gate.process(Uint8List(1)), isEmpty);
  });

  group('onset and close', () {
    Uint8List loud(int samples, [int amp = 3000]) {
      final b = Uint8List(samples * 2);
      for (var i = 0; i < samples; i++) {
        b[i * 2] = amp & 0xff;
        b[i * 2 + 1] = (amp >> 8) & 0xff;
      }
      return b;
    }

    Uint8List quiet(int samples) => Uint8List(samples * 2);

    test('the glasses profile needs speech to HOLD before it opens', () {
      var t = DateTime(2026, 1, 1);
      final gate = MicGate.glasses(clock: () => t);
      // 40 ms chunks (640 samples at 16 kHz) of voice-on-the-face level:
      // five loud chunks are not yet an onset…
      for (var i = 0; i < 5; i++) {
        expect(gate.process(loud(640, 9000)), isEmpty);
        expect(gate.isOpen, isFalse);
        t = t.add(const Duration(milliseconds: 40));
      }
      // …the sixth (240 ms of speech) opens it and flushes what it held.
      final out = gate.process(loud(640, 9000));
      expect(gate.isOpen, isTrue);
      expect(out.length, 6, reason: 'nothing of the onset is lost');
    });

    test('a loud blip that does not hold never opens the gate', () {
      var t = DateTime(2026, 1, 1);
      final gate = MicGate.glasses(clock: () => t);
      expect(gate.process(loud(640, 9000)), isEmpty);
      t = t.add(const Duration(milliseconds: 40));
      expect(gate.process(quiet(640)), isEmpty); // the blip ended
      t = t.add(const Duration(milliseconds: 40));
      expect(gate.process(loud(640, 9000)), isEmpty,
          reason: 'the count restarts');
      expect(gate.isOpen, isFalse);
    });

    test('a TV that never stops raises the glasses bar above itself', () {
      // Continuous background at 1200 RMS: above the phone-style minimum bar
      // (600 x 2.5 = 1500? no — 1200 < 1500, so use 1800 to be sure it is
      // loud enough to open at first) …
      var t = DateTime(2026, 1, 1);
      final gate = MicGate.glasses(clock: () => t);
      var opens = 0;
      gate.onOpen = (_, __) => opens++;
      // 20 s of a very loud, steady 6500-RMS TV (40 ms chunks). It opens
      // the gate at first (the floor starts at 2400, bar 6000) …
      for (var i = 0; i < 500; i++) {
        gate.process(loud(640, 6500));
        t = t.add(const Duration(milliseconds: 40));
      }
      expect(opens, greaterThan(0));
      // … but a gate held open that long is hearing a background: learning
      // resumed, the floor sits at its cap (2800) and the bar (7000) above
      // the TV, so the TV alone no longer holds the gate open.
      expect(gate.noiseFloor, closeTo(2800, 1));
      expect(gate.threshold, greaterThan(6500));
      // Let the hangover run out on more TV, then count new opens.
      for (var i = 0; i < 50; i++) {
        gate.process(loud(640, 6500));
        t = t.add(const Duration(milliseconds: 40));
      }
      expect(gate.isOpen, isFalse, reason: 'steady TV is the room now');
      final before = opens;
      // The wearer's own voice (9000 RMS, 320 ms) still gets through.
      for (var i = 0; i < 8; i++) {
        gate.process(loud(640, 9000));
        t = t.add(const Duration(milliseconds: 40));
      }
      expect(opens, before + 1);
      expect(gate.isOpen, isTrue);
    });

    test('a long sentence does not teach the glasses gate that speech is the room', () {
      var t = DateTime(2026, 1, 1);
      final gate = MicGate.glasses(clock: () => t);
      // 3 s of quiet room first (so the window is trusted) …
      for (var i = 0; i < 75; i++) {
        gate.process(quiet(640));
        t = t.add(const Duration(milliseconds: 40));
      }
      // … then 10 s of continuous speech at 8000 RMS: the floor must stay
      // put and the gate stay open for the whole sentence.
      for (var i = 0; i < 250; i++) {
        gate.process(loud(640, 8000));
        t = t.add(const Duration(milliseconds: 40));
      }
      expect(gate.isOpen, isTrue);
      expect(gate.noiseFloor, closeTo(2400, 1));
    });

    test('musicBoost lifts the bar while music plays and drops it after', () {
      final gate = MicGate.glasses();
      final bar = gate.threshold;
      gate.musicBoost = 1.6;
      expect(gate.threshold, closeTo(bar * 1.6, 1));
      gate.musicBoost = 1.0;
      expect(gate.threshold, closeTo(bar, 1));
    });

    test('the phone profile still opens on the first loud chunk', () {
      final gate = MicGate();
      expect(gate.process(loud(320)), isNotEmpty);
      expect(gate.isOpen, isTrue);
    });

    test('onClose fires once when the hangover expires, and on reset', () {
      var t = DateTime(2026, 1, 1);
      final gate = MicGate(clock: () => t);
      var closes = 0;
      gate.onClose = () => closes++;
      gate.process(loud(320));
      expect(gate.isOpen, isTrue);
      t = t.add(const Duration(milliseconds: 500));
      gate.process(quiet(320)); // inside the hangover: still open
      expect(closes, 0);
      t = t.add(const Duration(seconds: 1));
      gate.process(quiet(320)); // hangover over
      expect(closes, 1);
      gate.process(quiet(320));
      expect(closes, 1, reason: 'closed gates do not close again');
      gate.process(loud(320));
      gate.reset(); // the speaker started: the utterance ends here
      expect(closes, 2);
    });
  });
}
