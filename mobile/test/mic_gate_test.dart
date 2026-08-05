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
}
