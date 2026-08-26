/// Connected glasses and audible glasses are not the same thing.
///
/// The BLE control link carries battery, heartbeats, photos and the mic. The
/// assistant's VOICE goes out over classic Bluetooth (A2DP), which is a
/// separate connection — and on a phone the glasses were never paired with, it
/// never comes up at all. `connectClassicAudio` fired a reflective `connect()`
/// and threw the result away, so the app showed a healthy green "L801 100%"
/// while Farry spoke from the phone's own loudspeaker (device-seen 2026-08-26,
/// on a fresh install on a second phone).
///
/// Nothing was logged, because from the app's point of view nothing failed.
library;

import 'package:farryon/capture/glasses_capture_source.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('what the status carries', () {
    test('a fresh status does not claim to know about the audio link', () {
      const status = GlassesStatus(connected: true, battery: 100);
      expect(status.audioReady, isNull,
          reason: 'not asked yet is not the same as working');
      expect(status.audioPaired, isNull);
    });

    test('control link up + audio link down is representable', () {
      // The state the app could not previously express, which is exactly why
      // it displayed the wrong one.
      const status = GlassesStatus(
        connected: true,
        battery: 100,
        audioReady: false,
        audioPaired: false,
      );
      expect(status.connected, isTrue);
      expect(status.audioReady, isFalse);
      expect(status.audioPaired, isFalse,
          reason: 'unpaired is the actionable case — the wearer must pair '
              'them, retrying cannot help');
    });

    test('paired but not yet connected is distinct from never paired', () {
      // One of these is worth waiting out; the other needs the wearer to go to
      // Bluetooth settings. Collapsing them would put the app back to guessing.
      const settling = GlassesStatus(
        connected: true,
        audioReady: false,
        audioPaired: true,
      );
      const unpaired = GlassesStatus(
        connected: true,
        audioReady: false,
        audioPaired: false,
      );
      expect(settling.audioPaired, isNotNull);
      expect(settling.audioPaired, isNot(unpaired.audioPaired));
    });

    test('copyWith carries the audio fields', () {
      const status = GlassesStatus(connected: true);
      final updated = status.copyWith(audioReady: true, audioPaired: true);
      expect(updated.audioReady, isTrue);
      expect(updated.audioPaired, isTrue);
      expect(updated.connected, isTrue, reason: 'and does not lose the rest');
    });
  });
}
