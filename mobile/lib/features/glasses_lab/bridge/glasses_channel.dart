import 'dart:async';

import 'package:flutter/services.dart';

/// Typed Dart client for the `com.farryon/glasses` platform channels.
///
/// This is the ONLY place the Glasses Lab talks to native code, and the same
/// contract will later back [GlassesCaptureSource] (Stage B). The native side
/// lives in `android/app/src/main/kotlin/com/farryon/farryon/glasses/` and is
/// backed by either the vendor HeyCyan SDK (.aar in `android/app/libs/`) or a
/// stub that simulates a device so the Lab runs on any phone/emulator.
///
/// Design rule: commands are fire-and-acknowledge; device DATA (battery,
/// thumbnails, PCM, sync progress…) always arrives as events on the event
/// channel — mirroring how BLE actually behaves, so swapping the stub for the
/// real SDK changes no Dart code.
abstract class GlassesBridgeApi {
  /// Which native implementation is live: `stub` | `heycyan` — and its version.
  Future<Map<String, Object?>> bridgeInfo();

  /// BLE scan; resolves with everything found within [timeout].
  Future<List<GlassesDeviceHit>> scan(
      {Duration timeout = const Duration(seconds: 8)});

  Future<void> connect(String mac);

  Future<void> disconnect();

  Future<void> setAutoReconnect(bool enabled);

  /// Ask for a battery report — answer arrives as a `battery` event.
  Future<void> requestBattery();

  /// Ask for firmware/hardware versions — arrives as a `deviceInfo` event.
  Future<void> requestDeviceInfo();

  /// Plain photo onto glasses storage (no transfer).
  Future<void> takePhoto();

  /// AI photo: capture + BLE thumbnail. Returns a requestId; the JPEG comes
  /// back as a `thumbnail` event carrying the same requestId + elapsedMs.
  Future<String> takeAiPhoto();

  /// Classic Bluetooth (A2DP/HFP) bond for music/call audio routes.
  Future<void> pairClassicBt();

  /// Audio test paths: `hfp` (headset route), `pcm` (SDK voiceFromGlasses
  /// stream → `pcmChunk` events), `tts` (play a sample to the glasses).
  Future<void> startAudioTest(String mode);

  Future<void> stopAudioTest();

  Future<void> startWifiSync();

  Future<void> stopWifiSync();

  /// [type]: `music` | `call` | `system`.
  Future<void> setVolume(String type, int level);

  /// Broadcast stream of device events (see [GlassesLabEvent.type] values).
  Stream<GlassesLabEvent> events();
}

/// One BLE scan result.
class GlassesDeviceHit {
  const GlassesDeviceHit(
      {required this.name, required this.mac, required this.rssi});

  factory GlassesDeviceHit.fromMap(Map<dynamic, dynamic> m) => GlassesDeviceHit(
        name: (m['name'] as String?) ?? 'Unknown',
        mac: (m['mac'] as String?) ?? '',
        rssi: (m['rssi'] as num?)?.toInt() ?? 0,
      );

  final String name;
  final String mac;
  final int rssi;
}

/// A single event from the native side.
///
/// Known [type] values (the console card shows unknown ones too, so new
/// firmware events are never silently lost):
/// `connectionState` {state}, `battery` {pct, charging}, `wearState` {worn},
/// `gesture` {kind}, `deviceInfo` {btFirmware, wifiFirmware, hardware},
/// `thumbnail` {requestId, jpeg, elapsedMs}, `pcmChunk` {bytes, sampleRate},
/// `syncProgress` {file, pct, speedKbps}, `audio` {status}, `error` {message},
/// `deviceEvent` {hex} (raw/unmapped notifications).
class GlassesLabEvent {
  GlassesLabEvent({required this.type, required this.data, DateTime? at})
      : at = at ?? DateTime.now();

  factory GlassesLabEvent.fromMap(Map<dynamic, dynamic> m) => GlassesLabEvent(
        type: (m['type'] as String?) ?? 'unknown',
        data: Map<String, Object?>.from(
            (m['data'] as Map<dynamic, dynamic>?) ?? const <String, Object?>{}),
      );

  final String type;
  final Map<String, Object?> data;
  final DateTime at;

  /// Compact single line for the event console / exported log.
  String format() {
    final t = at.toIso8601String().substring(11, 23);
    if (type == 'thumbnail') {
      final size = (data['jpeg'] is Uint8List)
          ? '${(data['jpeg'] as Uint8List).length} B'
          : '?';
      return '$t  thumbnail  jpeg=$size elapsedMs=${data['elapsedMs']}';
    }
    final kv = data.entries.map((e) => '${e.key}=${e.value}').join(' ');
    return '$t  $type  $kv';
  }
}

/// [MethodChannel]-backed implementation used by the app.
class GlassesChannel implements GlassesBridgeApi {
  /// [methodChannel] / [eventStream] are injectable for tests only.
  GlassesChannel({
    MethodChannel? methodChannel,
    Stream<dynamic>? eventStream,
  })  : _method = methodChannel ?? const MethodChannel('com.farryon/glasses'),
        _rawEvents = eventStream ??
            const EventChannel('com.farryon/glasses/events')
                .receiveBroadcastStream();

  final MethodChannel _method;
  final Stream<dynamic> _rawEvents;

  @override
  Future<Map<String, Object?>> bridgeInfo() async {
    final m = await _method.invokeMapMethod<String, Object?>('bridgeInfo');
    return m ?? const {'implementation': 'unknown'};
  }

  @override
  Future<List<GlassesDeviceHit>> scan(
      {Duration timeout = const Duration(seconds: 8)}) async {
    final hits = await _method.invokeListMethod<dynamic>(
        'scan', {'timeoutMs': timeout.inMilliseconds});
    return (hits ?? const [])
        .whereType<Map<dynamic, dynamic>>()
        .map(GlassesDeviceHit.fromMap)
        .toList();
  }

  @override
  Future<void> connect(String mac) =>
      _method.invokeMethod<void>('connect', {'mac': mac});

  @override
  Future<void> disconnect() => _method.invokeMethod<void>('disconnect');

  @override
  Future<void> setAutoReconnect(bool enabled) =>
      _method.invokeMethod<void>('setAutoReconnect', {'enabled': enabled});

  @override
  Future<void> requestBattery() =>
      _method.invokeMethod<void>('requestBattery');

  @override
  Future<void> requestDeviceInfo() =>
      _method.invokeMethod<void>('requestDeviceInfo');

  @override
  Future<void> takePhoto() => _method.invokeMethod<void>('takePhoto');

  @override
  Future<String> takeAiPhoto() async =>
      (await _method.invokeMethod<String>('takeAiPhoto')) ?? '';

  @override
  Future<void> pairClassicBt() => _method.invokeMethod<void>('pairClassicBt');

  @override
  Future<void> startAudioTest(String mode) =>
      _method.invokeMethod<void>('startAudioTest', {'mode': mode});

  @override
  Future<void> stopAudioTest() => _method.invokeMethod<void>('stopAudioTest');

  @override
  Future<void> startWifiSync() => _method.invokeMethod<void>('startWifiSync');

  @override
  Future<void> stopWifiSync() => _method.invokeMethod<void>('stopWifiSync');

  @override
  Future<void> setVolume(String type, int level) =>
      _method.invokeMethod<void>('setVolume', {'type': type, 'level': level});

  @override
  Stream<GlassesLabEvent> events() => _rawEvents
      .where((e) => e is Map)
      .map((e) => GlassesLabEvent.fromMap(e as Map<dynamic, dynamic>));
}
