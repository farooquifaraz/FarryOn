import 'dart:async';
import 'dart:typed_data';

import '../core/logger.dart';
import '../features/glasses_lab/bridge/glasses_channel.dart';
import '../protocol/messages.dart';
import 'capture_source.dart';

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
/// **Vision:** the glasses do NOT produce a continuous 1 fps video stream —
/// they are photo-trigger only (AI photo → BLE thumbnail, median 3.8 s). So
/// [capabilities.videoIn] is false here; the split-seam vision selector +
/// on-demand photo trigger arrive in B1-B / B3. [jpegFrames] stays silent.
class GlassesCaptureSource implements CaptureSource {
  GlassesCaptureSource({
    this.deviceId = 'heycyan-l801',
    GlassesBridgeApi? bridge,
  }) : _bridge = bridge ?? GlassesChannel();

  static final _log = Logger('GlassesCapture');

  /// Stable identifier reported in `hello.device.id`.
  final String deviceId;

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

  void _pushStatus(GlassesStatus s) {
    _lastStatus = s;
    if (!_statusController.isClosed) _statusController.add(s);
  }

  @override
  CaptureCapabilities get capabilities =>
      // Audio-in only for now: glasses mic works; continuous video does not
      // exist on this hardware (photo-trigger arrives via the vision selector).
      const CaptureCapabilities(audioIn: true, videoIn: false);

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
    // Photo-trigger only — no continuous stream. The vision selector + AI
    // photo trigger land in B1-B / B3; jpegFrames stays silent here.
    _log.info('glasses startVideo: photo-trigger only, no continuous stream');
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
  Future<double> setZoom(double level) async => level < 1.0 ? 1.0 : level;

  /// Whether the glasses link is currently up (for UI/debug).
  bool get isConnected => _connectedMac != null;
  bool get isAudioRunning => _audioRunning;

  @override
  Future<void> dispose() async {
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
