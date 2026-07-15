import 'dart:async';
import 'dart:typed_data';

import '../core/logger.dart';
import '../features/glasses_lab/bridge/glasses_channel.dart';
import '../protocol/messages.dart';
import 'capture_source.dart';
import 'glasses_capture_config.dart';

/// Why a glasses photo request failed. The [wire] codes are the contract
/// shared with the native bridge (`captureFailed` events) and the backend
/// (`capture_failed` control message / `capture_feedback.py`) — keep the
/// three in sync.
enum GlassesCaptureFailure {
  /// No BLE link (and none came up within the configured connect wait).
  notConnected('not_connected'),

  /// The glasses refused the command (already capturing / syncing / recording).
  busy('busy'),

  /// The glasses never reported the capture (native watchdog or Dart budget).
  captureTimeout('capture_timeout'),

  /// The photo was taken but the BLE thumbnail stream stalled mid-transfer.
  transferStalled('transfer_stalled'),

  /// The transfer completed but carried zero bytes.
  emptyImage('empty_image'),

  /// The capture command never reached the native side (channel error).
  commandFailed('command_failed'),

  /// A reason code this app build doesn't recognise (newer native side).
  unknown('unknown');

  const GlassesCaptureFailure(this.wire);

  /// Machine-readable reason code sent to the backend.
  final String wire;

  static GlassesCaptureFailure fromWire(String? code) =>
      GlassesCaptureFailure.values.firstWhere(
        (f) => f.wire == code,
        orElse: () => GlassesCaptureFailure.unknown,
      );
}

/// Outcome of one [GlassesCaptureSource.capturePhoto] request: either the
/// JPEG that was (also) emitted on [GlassesCaptureSource.jpegFrames], or a
/// typed failure the caller can report.
class GlassesCaptureResult {
  const GlassesCaptureResult.success(Uint8List this.jpeg) : failure = null;

  const GlassesCaptureResult.failed(GlassesCaptureFailure this.failure)
      : jpeg = null;

  final Uint8List? jpeg;
  final GlassesCaptureFailure? failure;

  bool get ok => failure == null;
}

/// Smart-glasses implementation of [CaptureSource], backed by the real HeyCyan
/// bridge (`com.farryon/glasses`) proven in Stage A — the same transport the
/// Glasses Lab uses.
///
/// **Audio (B1):** the glasses mic streams PCM 16 kHz / 16-bit / mono over BLE
/// (hardware-verified), exactly the [CaptureSource] contract — forwarded to
/// [audio16k] with zero resampling. Note the glasses are **push-to-talk**:
/// [startAudio] arms the PCM path but bytes only flow while the user
/// long-presses the temple (voiceFromGlassesStatus 1→2). That matches the
/// "long-press-to-talk" glasses combo.
///
/// **Vision (B3):** the glasses do NOT produce a continuous 1 fps stream —
/// they are photo-trigger only (AI photo → BLE thumbnail, median 3.8 s). So
/// [capabilities.videoIn] is true but [jpegFrames] only emits when
/// [capturePhoto] is called (voice `capture_photo` tool or shutter button);
/// that one frame then flows the same path a phone-camera frame does.
class GlassesCaptureSource implements CaptureSource {
  GlassesCaptureSource({
    this.deviceId = 'heycyan-l801',
    GlassesBridgeApi? bridge,
    this.config = const GlassesCaptureConfig(),
  }) : _bridge = bridge ?? GlassesChannel.shared;

  static final _log = Logger('GlassesCapture');

  /// Stable identifier reported in `hello.device.id`.
  final String deviceId;

  /// Timing budgets for the capture pipeline (injectable for tests).
  final GlassesCaptureConfig config;

  final GlassesBridgeApi _bridge;

  final _audioController = StreamController<Uint8List>.broadcast();
  final _videoController = StreamController<Uint8List>.broadcast();

  /// Live glasses status for the UI (connection + battery + whether mic PCM
  /// is currently flowing). B1-C surfaces this as a banner in the live screen.
  final _statusController = StreamController<GlassesStatus>.broadcast();
  Stream<GlassesStatus> get status => _statusController.stream;
  GlassesStatus _lastStatus = const GlassesStatus();

  StreamSubscription<GlassesLabEvent>? _eventSub;
  bool _audioRunning = false;
  String? _connectedMac;

  /// The single in-flight capture request (see [capturePhoto]): the SDK has
  /// one thumbnail callback slot, so requests are never issued concurrently —
  /// duplicate triggers join the pending Future instead.
  Completer<GlassesCaptureResult>? _inFlight;

  /// Native requestId of the in-flight capture, used to correlate the
  /// `thumbnail` / `captureFailed` events back to the awaiting Future.
  String? _inFlightRequestId;

  /// Last-resort budget timer above the native watchdogs.
  Timer? _captureTimer;

  /// Automatic re-captures left for the in-flight request after a retryable
  /// failure (transfer stalled / busy / empty image).
  int _retriesLeft = 0;

  /// Delay timer before an automatic retry.
  Timer? _retryTimer;

  /// Failures worth an automatic re-capture: the photo itself is fine, only
  /// the BLE fetch was disrupted (double-notify / contention). NOT
  /// notConnected / captureTimeout / commandFailed — those need the user or a
  /// fresh connection, so retrying would just hang.
  static bool _isRetryable(GlassesCaptureFailure f) =>
      f == GlassesCaptureFailure.transferStalled ||
      f == GlassesCaptureFailure.busy ||
      f == GlassesCaptureFailure.emptyImage;

  void _pushStatus(GlassesStatus s) {
    _lastStatus = s;
    if (!_statusController.isClosed) _statusController.add(s);
  }

  @override
  CaptureCapabilities get capabilities =>
      // B3: glasses now expose vision too — not a continuous stream but an
      // on-demand photo (voice tool or shutter button) whose thumbnail is
      // emitted on [jpegFrames], so downstream it behaves like a phone-camera
      // frame (Gemini native vision + identify_image both work).
      const CaptureCapabilities(audioIn: true, videoIn: true);

  @override
  DeviceInfo get info => DeviceInfo(
        kind: 'glasses',
        id: deviceId,
        capabilities: capabilities.toWireCapabilities(),
      );

  @override
  Stream<Uint8List> get audio16k => _audioController.stream;

  @override
  Stream<Uint8List> get jpegFrames => _videoController.stream;

  @override
  Future<void> initialize() async {
    // One event subscription for the source's lifetime: PCM bytes → audio,
    // connection state tracked for auto-connect.
    _eventSub ??= _bridge.events().listen(_onEvent, onError: (Object e) {
      _log.warn('glasses event error: $e');
    });
    // Auto-connect to the last-paired glasses (saved MAC, no scan).
    try {
      final infoMap = await _bridge.bridgeInfo();
      final lastMac = infoMap['lastMac'] as String?;
      if (lastMac != null && lastMac.isNotEmpty) {
        _log.info('glasses: auto-connecting to saved $lastMac');
        await _bridge.connect(lastMac);
      } else {
        _log.info('glasses: no saved device — pick one in the Glasses Lab first');
      }
    } catch (e) {
      _log.warn('glasses initialize failed: $e');
    }
  }

  void _onEvent(GlassesLabEvent event) {
    switch (event.type) {
      case 'connectionState':
        final state = event.data['state'] as String?;
        _connectedMac =
            state == 'connected' ? event.data['mac'] as String? : null;
        _pushStatus(_lastStatus.copyWith(
          connected: _connectedMac != null,
          battery: _connectedMac == null ? null : _lastStatus.battery,
        ));
        // A drop mid-capture means the thumbnail can never arrive — fail the
        // awaiting request now instead of running out its budget.
        if (_connectedMac == null && _inFlightRequestId != null) {
          _completeCapture(const GlassesCaptureResult.failed(
              GlassesCaptureFailure.notConnected));
        }
      case 'battery':
        final pct = (event.data['pct'] as num?)?.toInt();
        if (pct != null) _pushStatus(_lastStatus.copyWith(battery: pct));
      case 'pcmChunk':
        if (!_audioRunning) return;
        if (!_lastStatus.talking) {
          _pushStatus(_lastStatus.copyWith(talking: true));
        }
        final data = event.data['data'];
        if (data is Uint8List && data.isNotEmpty) {
          _audioController.add(data);
        }
      case 'thumbnail':
        // B3: an AI/gesture photo finished — the JPEG thumbnail is exactly the
        // frame Stage B feeds to vision. Emit it on jpegFrames so it flows the
        // same path a phone-camera frame does (→ sendVideo → Gemini + backend
        // last_frame cache). An empty jpeg is only the native Lab-spinner
        // marker; the typed `captureFailed` event carries the reason.
        final jpeg = event.data['jpeg'];
        if (jpeg is Uint8List && jpeg.isNotEmpty) {
          _log.info('glasses photo → ${jpeg.length} bytes (emitting as frame)');
          if (!_videoController.isClosed) _videoController.add(jpeg);
          if (_inFlightRequestId != null &&
              event.data['requestId'] == _inFlightRequestId) {
            _completeCapture(GlassesCaptureResult.success(jpeg));
          }
        }
      case 'captureFailed':
        // Typed failure from the native bridge (busy / capture_timeout /
        // transfer_stalled / empty_image), correlated by requestId.
        final reason = event.data['reason'] as String?;
        _log.warn('glasses captureFailed '
            'requestId=${event.data['requestId']} reason=$reason');
        if (_inFlightRequestId != null &&
            event.data['requestId'] == _inFlightRequestId) {
          final failure = GlassesCaptureFailure.fromWire(reason);
          if (_isRetryable(failure) && _retriesLeft > 0) {
            // The glasses double-fired the capture notify and stalled that
            // transfer; a clean re-capture almost always works. Retry silently
            // instead of surfacing an error the user has to react to.
            _retriesLeft--;
            _log.info('capture retry (${failure.wire}), $_retriesLeft left');
            _captureTimer?.cancel();
            _inFlightRequestId = null;
            _retryTimer?.cancel();
            _retryTimer = Timer(config.retryDelay, () {
              final completer = _inFlight;
              if (completer != null && !completer.isCompleted) {
                unawaited(_issueCapture(completer));
              }
            });
          } else {
            _completeCapture(GlassesCaptureResult.failed(failure));
          }
        }
      case 'audio':
        // voiceFromGlassesStatus 2 (mic off) arrives as an `audio` status.
        final s = (event.data['status'] as String?) ?? '';
        if (s.contains('mic OFF') || s.contains('stopped')) {
          if (_lastStatus.talking) {
            _pushStatus(_lastStatus.copyWith(talking: false));
          }
        }
    }
  }

  /// B3: trigger an on-demand glasses photo. The capture runs on the headset
  /// (~3–4 s to the BLE thumbnail); when it lands it is emitted on
  /// [jpegFrames] AND returned here, correlated by the native requestId.
  ///
  /// Robustness contract:
  ///  * Single in-flight request — a duplicate trigger (e.g. the model calling
  ///    both `capture_photo` and `identify_image` in one turn) joins the
  ///    pending Future instead of issuing a second BLE command, which would
  ///    corrupt the SDK's single thumbnail stream.
  ///  * If the link is still coming up (session-start auto-connect), the
  ///    request waits up to [GlassesCaptureConfig.connectWait] before failing
  ///    with [GlassesCaptureFailure.notConnected].
  ///  * Always completes within [GlassesCaptureConfig.captureTimeout] — with
  ///    the native side's precise failure reason when one was reported.
  Future<GlassesCaptureResult> capturePhoto() {
    final pending = _inFlight;
    if (pending != null) {
      _log.info('capturePhoto: joining the in-flight request');
      return pending.future;
    }
    final completer = Completer<GlassesCaptureResult>();
    _inFlight = completer;
    unawaited(_runCapture(completer));
    return completer.future;
  }

  Future<void> _runCapture(Completer<GlassesCaptureResult> completer) async {
    // Session-start race: initialize()'s auto-connect may still be in
    // progress — give the link a bounded chance to come up.
    if (_connectedMac == null) {
      _log.info(
          'capturePhoto: not connected — waiting up to ${config.connectWait}');
      try {
        await status
            .firstWhere((s) => s.connected)
            .timeout(config.connectWait);
      } catch (_) {
        // Timeout or stream closed — the null check below reports it.
      }
    }
    if (completer.isCompleted) return; // e.g. disposed while waiting
    if (_connectedMac == null) {
      _log.warn('capturePhoto: glasses not connected');
      _completeCapture(const GlassesCaptureResult.failed(
          GlassesCaptureFailure.notConnected));
      return;
    }
    _retriesLeft = config.maxRetries;
    await _issueCapture(completer);
  }

  /// Fire one AI-photo command and arm the budget timer. Called for the first
  /// attempt and each automatic retry (see the `captureFailed` handler).
  Future<void> _issueCapture(Completer<GlassesCaptureResult> completer) async {
    if (completer.isCompleted) return;
    final String requestId;
    try {
      _log.info('glasses: taking AI photo…');
      requestId = await _bridge.takeAiPhoto();
    } catch (e) {
      _log.warn('glasses capturePhoto failed: $e');
      _completeCapture(const GlassesCaptureResult.failed(
          GlassesCaptureFailure.commandFailed));
      return;
    }
    if (completer.isCompleted) return;
    _inFlightRequestId = requestId;
    // Last-resort net: the native watchdogs normally report a typed failure
    // first (see GlassesCaptureConfig for the timeout ladder).
    _captureTimer?.cancel();
    _captureTimer = Timer(config.captureTimeout, () {
      _log.warn('capturePhoto: no result within ${config.captureTimeout}');
      _completeCapture(const GlassesCaptureResult.failed(
          GlassesCaptureFailure.captureTimeout));
    });
  }

  /// Resolve the in-flight request (idempotent) and clear its bookkeeping.
  void _completeCapture(GlassesCaptureResult result) {
    _captureTimer?.cancel();
    _captureTimer = null;
    _retryTimer?.cancel();
    _retryTimer = null;
    _retriesLeft = 0;
    _inFlightRequestId = null;
    final completer = _inFlight;
    _inFlight = null;
    if (completer != null && !completer.isCompleted) {
      completer.complete(result);
    }
  }

  @override
  Future<void> startAudio() async {
    _audioRunning = true;
    // Arm the glasses PCM path; bytes flow on long-press (push-to-talk).
    try {
      await _bridge.startAudioTest('pcm');
      _log.info('glasses audio armed (long-press the temple to talk)');
    } catch (e) {
      _log.warn('glasses startAudio failed: $e');
    }
  }

  @override
  Future<void> stopAudio() async {
    _audioRunning = false;
    try {
      await _bridge.stopAudioTest();
    } catch (e) {
      _log.warn('glasses stopAudio failed: $e');
    }
  }

  @override
  Future<void> startVideo() async {
    // Photo-trigger only — no continuous stream. Frames appear on [jpegFrames]
    // when [capturePhoto] is called (voice tool or shutter button, B3); there
    // is nothing to start here.
    _log.info('glasses startVideo: photo-trigger only (capturePhoto emits frames)');
  }

  @override
  Future<void> stopVideo() async {}

  @override
  Future<void> releaseCamera() async {}

  @override
  Future<void> setPortrait(bool portrait) async {
    // Orientation is fixed by the headset.
  }

  @override
  Future<void> setFrontCamera(bool front) async {
    // The glasses have a single fixed lens — no front/back to switch.
  }

  @override
  Future<double> setZoom(double level) async => level < 1.0 ? 1.0 : level;

  /// Whether the glasses link is currently up (for UI/debug).
  bool get isConnected => _connectedMac != null;
  bool get isAudioRunning => _audioRunning;

  @override
  Future<void> dispose() async {
    // Never leave a caller hanging on a request that can no longer complete.
    _completeCapture(const GlassesCaptureResult.failed(
        GlassesCaptureFailure.notConnected));
    await _eventSub?.cancel();
    _eventSub = null;
    try {
      await _bridge.disconnect();
    } catch (_) {
      // Best-effort teardown.
    }
    await _audioController.close();
    await _videoController.close();
    await _statusController.close();
  }
}

/// A snapshot of the glasses for the live-screen banner (B1-C).
class GlassesStatus {
  const GlassesStatus({
    this.connected = false,
    this.battery,
    this.talking = false,
  });

  final bool connected;
  final int? battery;

  /// True while glasses-mic PCM is actively streaming (user is long-pressing).
  final bool talking;

  GlassesStatus copyWith({bool? connected, int? battery, bool? talking}) =>
      GlassesStatus(
        connected: connected ?? this.connected,
        battery: battery ?? this.battery,
        talking: talking ?? this.talking,
      );
}
