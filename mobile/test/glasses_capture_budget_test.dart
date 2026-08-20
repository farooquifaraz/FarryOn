/// The capture budgets have to outlast the hardware, not our patience.
///
/// The chain only works if each outer layer waits longer than the one inside
/// it, so the layer that actually knows why a capture failed is the one that
/// reports it:
///
///   native watchdogs  <  backend GLASSES_FRAME_WAIT_SECONDS  <  captureTimeout
///
/// Break that order and a slow-but-real photo is reported as a failure by
/// whichever timer fires first. That is not hypothetical: on 2026-08-21 an L802
/// took 28 s to deliver a thumbnail (2.4 s to shoot, then 19 BLE chunks about
/// 830 ms apart). The backend gave up at 18 s and Dart at 22 s, so the model
/// told the wearer it could not see — while the photo landed six seconds later
/// and was quietly filed in the gallery. Nothing in the app said anything was
/// wrong, because from every timer's point of view nothing was.
library;

import 'package:farryon/capture/glasses_capture_config.dart';
import 'package:flutter_test/flutter_test.dart';

/// Mirrors `Settings.glasses_frame_wait_seconds` in `backend/app/config.py`.
/// The two move together; this constant exists so the ordering below can be
/// checked from the Dart side, and so raising one without the other fails here.
const Duration kBackendGlassesFrameWait = Duration(seconds: 32);

void main() {
  const config = GlassesCaptureConfig();

  test('the Dart backstop outlasts the backend frame wait', () {
    expect(
      config.captureTimeout,
      greaterThan(kBackendGlassesFrameWait),
      reason: 'a Dart timeout that fires first pre-empts the backend with a '
          'spurious captureFailed, and the real reason is lost',
    );
  });

  test('the budget covers a transfer as slow as the one measured', () {
    // 2.4 s to take the picture + 19 chunks at ~830 ms = ~28 s, measured on an
    // L802 in a live session with A2DP audio contending for the same radio.
    const measured = Duration(seconds: 28);
    expect(
      config.captureTimeout,
      greaterThan(measured),
      reason: 'a photo that arrives must be delivered, not reported as failed',
    );
    expect(
      kBackendGlassesFrameWait,
      greaterThan(measured),
      reason: 'the backend gives up first — it must not give up before the '
          'glasses are done',
    );
  });

  test('the connect wait still covers a slow BLE connect', () {
    // Unchanged, and checked here so a future edit to this file notices it:
    // BLE connect is ~2.5 s median, 5.05 s worst measured.
    expect(config.connectWait, greaterThanOrEqualTo(const Duration(seconds: 6)));
  });
}
