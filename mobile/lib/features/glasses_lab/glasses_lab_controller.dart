import 'dart:async';

import 'package:flutter/foundation.dart';

import '../../core/logger.dart';
import 'bridge/glasses_channel.dart';
import 'glasses_permissions.dart';

/// One received thumbnail (AI-photo result) with its measured latency.
class LabThumbnail {
  LabThumbnail({
    required this.jpeg,
    required this.elapsedMs,
    DateTime? at,
  }) : at = at ?? DateTime.now();

  final Uint8List jpeg;
  final int elapsedMs;
  final DateTime at;
}

/// State + actions for the Glasses Lab screen.
///
/// Pure ChangeNotifier over the [GlassesBridgeApi] contract — no platform
/// code — so the whole Lab is unit-testable with a fake bridge. Every action
/// is wrapped so a missing/broken native side degrades to a visible status
/// line instead of a crash (the Lab must never destabilise the app).
class GlassesLabController extends ChangeNotifier {
  GlassesLabController(this._bridge,
      {Future<bool> Function()? ensureBlePermissions,
      Future<bool> Function()? ensureWifiPermissions})
      : _ensureBlePermissions =
            ensureBlePermissions ?? requestGlassesBlePermissions,
        _ensureWifiPermissions =
            ensureWifiPermissions ?? requestGlassesWifiPermissions {
    _sub = _bridge.events().listen(_onEvent, onError: (Object e) {
      _logEvent(GlassesLabEvent(type: 'error', data: {'message': '$e'}));
    });
    _loadBridgeInfo();
  }

  static final _log = Logger('GlassesLab');

  /// Cap so an event-happy device can't grow memory unbounded.
  static const int maxEvents = 500;

  /// Keep the last few thumbnails for the camera card's strip.
  static const int maxThumbnails = 5;

  final GlassesBridgeApi _bridge;
  final Future<bool> Function() _ensureBlePermissions;
  final Future<bool> Function() _ensureWifiPermissions;
  StreamSubscription<GlassesLabEvent>? _sub;

  /// True after the user refused the Bluetooth runtime permissions — the
  /// Connection card shows a red banner until a later Scan tap succeeds.
  bool blePermissionDenied = false;

  // -- Bridge / connection state -------------------------------------------
  String bridgeImplementation = '…';
  String sdkVersion = '';
  bool get bridgeAvailable =>
      bridgeImplementation != 'unavailable' && bridgeImplementation != '…';

  bool scanning = false;
  List<GlassesDeviceHit> devices = const [];
  String connectionState = 'disconnected'; // disconnected|connecting|connected
  String? connectedMac;
  bool autoReconnect = true;

  // -- Device info -----------------------------------------------------------
  int? batteryPct;
  bool charging = false;
  bool? worn;
  Map<String, Object?> deviceInfo = const {};

  // -- Camera ----------------------------------------------------------------
  List<LabThumbnail> thumbnails = [];
  bool photoInFlight = false;

  // -- Audio -----------------------------------------------------------------
  String? audioMode; // null | hfp | pcm | tts
  int pcmChunks = 0;
  int? pcmSampleRate;

  // -- Media sync -------------------------------------------------------------
  bool syncing = false;
  String? syncFile;
  int syncPct = 0;
  double syncSpeedKbps = 0;

  // -- Event console -----------------------------------------------------------
  final List<GlassesLabEvent> events = [];
  String? lastError;

  Future<void> _loadBridgeInfo() => _guard('bridgeInfo', () async {
        final info = await _bridge.bridgeInfo();
        bridgeImplementation = (info['implementation'] as String?) ?? 'unknown';
        sdkVersion = (info['sdkVersion'] as String?) ?? '';
      });

  // -- Actions ----------------------------------------------------------------

  Future<void> startScan() => _guard('scan', () async {
        final granted = await _ensureBlePermissions();
        blePermissionDenied = !granted;
        if (!granted) {
          _logEvent(GlassesLabEvent(
            type: 'error',
            data: {'message': 'Bluetooth permissions denied — scan skipped'},
          ));
          return;
        }
        scanning = true;
        devices = const [];
        notifyListeners();
        devices = await _bridge.scan();
        scanning = false;
      });

  Future<void> connect(String mac) => _guard('connect', () async {
        connectionState = 'connecting';
        connectedMac = mac;
        notifyListeners();
        await _bridge.connect(mac);
        // Final state arrives as a `connectionState` event.
      });

  Future<void> disconnect() => _guard('disconnect', _bridge.disconnect);

  Future<void> toggleAutoReconnect(bool enabled) =>
      _guard('setAutoReconnect', () async {
        autoReconnect = enabled;
        await _bridge.setAutoReconnect(enabled);
      });

  Future<void> refreshDeviceInfo() => _guard('refreshDeviceInfo', () async {
        await _bridge.requestBattery();
        await _bridge.requestDeviceInfo();
      });

  Future<void> takePhoto() => _guard('takePhoto', _bridge.takePhoto);

  Future<void> takeAiPhoto() => _guard('takeAiPhoto', () async {
        photoInFlight = true;
        notifyListeners();
        await _bridge.takeAiPhoto();
        // Thumbnail arrives as a `thumbnail` event (photoInFlight clears there).
      });

  Future<void> pairClassicBt() => _guard('pairClassicBt', _bridge.pairClassicBt);

  Future<void> startAudioTest(String mode) => _guard('audio:$mode', () async {
        audioMode = mode;
        pcmChunks = 0;
        pcmSampleRate = null;
        await _bridge.startAudioTest(mode);
      });

  Future<void> stopAudioTest() => _guard('stopAudioTest', () async {
        audioMode = null;
        await _bridge.stopAudioTest();
      });

  Future<void> startWifiSync() => _guard('startWifiSync', () async {
        final granted = await _ensureWifiPermissions();
        if (!granted) {
          _logEvent(GlassesLabEvent(
            type: 'error',
            data: {'message': 'Nearby-WiFi permission denied — sync skipped'},
          ));
          return;
        }
        syncing = true;
        syncPct = 0;
        await _bridge.startWifiSync();
      });

  Future<void> stopWifiSync() => _guard('stopWifiSync', () async {
        syncing = false;
        await _bridge.stopWifiSync();
      });

  Future<void> setVolume(String type, int level) =>
      _guard('setVolume', () => _bridge.setVolume(type, level));

  void clearEvents() {
    events.clear();
    notifyListeners();
  }

  /// Full console text for copy/share.
  String exportEvents() => events.map((e) => e.format()).join('\n');

  // -- Event ingestion -----------------------------------------------------

  void _onEvent(GlassesLabEvent event) {
    switch (event.type) {
      case 'connectionState':
        connectionState = (event.data['state'] as String?) ?? connectionState;
        if (connectionState == 'disconnected') connectedMac = null;
      case 'battery':
        batteryPct = (event.data['pct'] as num?)?.toInt();
        charging = event.data['charging'] == true;
      case 'wearState':
        worn = event.data['worn'] == true;
      case 'deviceInfo':
        deviceInfo = Map<String, Object?>.from(event.data);
      case 'thumbnail':
        photoInFlight = false;
        final jpeg = event.data['jpeg'];
        if (jpeg is Uint8List && jpeg.isNotEmpty) {
          thumbnails.insert(
            0,
            LabThumbnail(
              jpeg: jpeg,
              elapsedMs: (event.data['elapsedMs'] as num?)?.toInt() ?? -1,
            ),
          );
          if (thumbnails.length > maxThumbnails) {
            thumbnails = thumbnails.sublist(0, maxThumbnails);
          }
        }
      case 'pcmChunk':
        pcmChunks++;
        pcmSampleRate =
            (event.data['sampleRate'] as num?)?.toInt() ?? pcmSampleRate;
        // Log only a 1-in-50 sample — 10 chunks/sec would drown the console.
        if (pcmChunks % 50 != 1) {
          notifyListeners();
          return;
        }
      case 'syncProgress':
        syncFile = event.data['file'] as String?;
        syncPct = (event.data['pct'] as num?)?.toInt() ?? syncPct;
        syncSpeedKbps =
            (event.data['speedKbps'] as num?)?.toDouble() ?? syncSpeedKbps;
        if (syncPct >= 100) syncing = false;
      case 'error':
        lastError = event.data['message'] as String?;
    }
    _logEvent(event);
  }

  void _logEvent(GlassesLabEvent event) {
    events.add(event);
    if (events.length > maxEvents) {
      events.removeRange(0, events.length - maxEvents);
    }
    notifyListeners();
  }

  /// Run [action]; surface any failure in the console + status line instead
  /// of throwing into the UI. A MissingPluginException means the native
  /// bridge isn't registered on this platform (e.g. iOS today).
  Future<void> _guard(String name, Future<void> Function() action) async {
    try {
      await action();
    } catch (e) {
      _log.warn('$name failed: $e');
      scanning = false;
      photoInFlight = false;
      if ('$e'.contains('MissingPluginException')) {
        bridgeImplementation = 'unavailable';
      }
      lastError = '$name: $e';
      _logEvent(GlassesLabEvent(type: 'error', data: {'message': lastError}));
      return;
    }
    notifyListeners();
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }
}
