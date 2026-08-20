/// Central tuning for the glasses photo-capture pipeline.
///
/// Every timing knob the capture path uses lives here — call sites never carry
/// inline durations — so budgets can be tuned in one place and injected small
/// in tests. The success path is event-driven — a delivered thumbnail (or a
/// native captureFailed) resolves the request immediately — so these are only
/// failure backstops, and each outer layer outlasts the inner one so the layer
/// that actually knows the failure reason reports it first:
///
///   native capture watchdog (8 s, command to notify)
///   + rolling BLE transfer watchdog (3 s/chunk, stall detection)
///     < backend `GLASSES_FRAME_WAIT_SECONDS` (32 s)
///     < [captureTimeout] (Dart, 38 s)
///
/// The Dart backstop sits ABOVE the backend budget on purpose: a slow but real
/// transfer must resolve as a delivered photo, never be pre-empted by a Dart
/// timeout firing a spurious captureFailed while the backend would still
/// accept the frame.
///
/// The numbers come from the hardware, and the hardware moved. They were set
/// against 10-12 s (2026-07-11). Re-measured on an L802 mid-session
/// (2026-08-21): the glasses take the picture in 2.4 s and then send the
/// thumbnail over BLE in 19 chunks about 830 ms apart — 28 s in total. The old
/// 22 s backstop fired six seconds early, so a photo that had genuinely been
/// taken was reported as a failure, and the model told the wearer it could not
/// see. Anything tuned here must be checked against a real transfer, not
/// against how long a wait feels acceptable.
class GlassesCaptureConfig {
  const GlassesCaptureConfig({
    this.captureTimeout = const Duration(seconds: 38),
    this.connectWait = const Duration(seconds: 6),
    this.maxRetries = 1,
    this.retryDelay = const Duration(milliseconds: 800),
  });

  /// Total backstop for one photo request (BLE command to JPEG thumbnail).
  /// Sits above the backend frame-wait budget so a slow-but-real transfer is
  /// never pre-empted; the native watchdogs report a typed failure well before
  /// it fires in the genuine-failure case.
  final Duration captureTimeout;

  /// How long a capture request waits for an in-progress connection before
  /// failing with `notConnected`. Covers the session-start race: BLE connect
  /// takes ~2.5 s median (worst measured 5.05 s), and the user often asks
  /// "what is this?" immediately.
  final Duration connectWait;

  /// How many times a request silently re-issues the AI photo after a
  /// RETRYABLE failure (transfer stalled / glasses busy / empty image) before
  /// giving up. The glasses occasionally double-fire the capture notify, which
  /// stalls that transfer (device-proven 2026-07-11 — ~20% under A2DP radio
  /// contention); a clean re-capture almost always succeeds, so one automatic
  /// retry turns most of those into a successful photo with no user action.
  final int maxRetries;

  /// Pause before an automatic retry, to let the glasses settle after the
  /// stalled/duplicated transfer.
  final Duration retryDelay;
}
