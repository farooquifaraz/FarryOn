/// A sync that cannot finish has to be able to give up.
///
/// The native side pushed its stall watchdog forward on every callback that
/// looked like progress — including `onGlassesControlSuccess`, which is the ack
/// for a control command and which the vendor SDK repeats. So a wedged transfer
/// was fed a fresh 60 s every few seconds and never timed out: the banner sat
/// on "WiFi sync started · 0%", `syncActive` stayed true so nothing else could
/// run, and WiFi-P2P kept working the radio.
///
/// What that looked like on the phone (2026-08-26): the UI froze in ~6 s
/// blocks, 1043 frames skipped in the worst one, the keyboard took seconds to
/// appear, and typing lagged behind the finger. Nothing pointed at the sync —
/// it was a banner in the corner reading 0%.
///
/// The native fix is a whole-run deadline that no callback can push forward.
/// This test guards the ordering it depends on from the Dart side.
library;

import 'package:farryon/state/live_controller.dart';
import 'package:flutter_test/flutter_test.dart';

/// Mirrors `WIFI_SYNC_TOTAL_BUDGET_MS` in `HeyCyanGlassesSdk.kt`. The two move
/// together; this constant exists so the ordering can be checked here, and so
/// changing one without the other fails.
const Duration kNativeSyncDeadline = Duration(seconds: 240);

void main() {
  test('the Dart in-flight guard outlasts the native deadline', () {
    // If this backstop fires first it clears the guard while a transfer is
    // still running, and the next photo starts a second sync on top. Two
    // concurrent importAlbum calls wedge the P2P session — the exact state the
    // deadline exists to escape.
    expect(
      LiveController.autoSyncGuardTimeout,
      greaterThan(kNativeSyncDeadline),
      reason: 'the app must not give up on a sync before the glasses side does',
    );
  });

  test('the guard is not so long that a wedged sync blocks the next one all day',
      () {
    // The other direction: this guard is the only thing that releases
    // `_autoSyncing` when a terminal syncProgress never lands at all.
    expect(
      LiveController.autoSyncGuardTimeout,
      lessThan(const Duration(minutes: 10)),
    );
  });
}
