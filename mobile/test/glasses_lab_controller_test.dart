import 'dart:async';
import 'dart:typed_data';

import 'package:farryon/features/glasses_lab/bridge/glasses_channel.dart';
import 'package:farryon/features/glasses_lab/glasses_lab_controller.dart';
import 'package:flutter/services.dart' show MissingPluginException;
import 'package:flutter_test/flutter_test.dart';

/// Fake bridge: records calls, lets tests push device events.
class _FakeBridge implements GlassesBridgeApi {
  final calls = <String>[];
  final eventController = StreamController<GlassesLabEvent>.broadcast();
  Map<String, Object?> bridgeInfoExtra = const {};
  List<GlassesDeviceHit> scanResult = const [
    GlassesDeviceHit(name: 'L801-TEST', mac: 'AA:BB', rssi: -50),
  ];
  bool failNext = false;

  void emit(String type, Map<String, Object?> data) =>
      eventController.add(GlassesLabEvent(type: type, data: data));

  Future<void> _maybeFail(String name) async {
    calls.add(name);
    if (failNext) {
      failNext = false;
      throw MissingPluginException('No implementation found for $name');
    }
  }

  @override
  Future<Map<String, Object?>> bridgeInfo() async {
    await _maybeFail('bridgeInfo');
    return {
      'implementation': 'stub',
      'sdkVersion': 'sim-1.0',
      ...bridgeInfoExtra,
    };
  }

  @override
  Future<List<GlassesDeviceHit>> scan(
      {Duration timeout = const Duration(seconds: 8)}) async {
    await _maybeFail('scan');
    return scanResult;
  }

  @override
  Future<void> connect(String mac) => _maybeFail('connect:$mac');

  @override
  Future<void> disconnect() => _maybeFail('disconnect');

  @override
  Future<void> setAutoReconnect(bool enabled) =>
      _maybeFail('setAutoReconnect:$enabled');

  @override
  Future<void> requestBattery() => _maybeFail('requestBattery');

  @override
  Future<void> requestDeviceInfo() => _maybeFail('requestDeviceInfo');

  @override
  Future<void> takePhoto() => _maybeFail('takePhoto');

  @override
  Future<String> takeAiPhoto() async {
    await _maybeFail('takeAiPhoto');
    return 'req-1';
  }

  @override
  Future<void> pairClassicBt() => _maybeFail('pairClassicBt');

  @override
  Future<void> startAudioTest(String mode) => _maybeFail('startAudioTest:$mode');

  @override
  Future<void> stopAudioTest() => _maybeFail('stopAudioTest');

  @override
  Future<void> startWifiSync() => _maybeFail('startWifiSync');

  @override
  Future<void> stopWifiSync() => _maybeFail('stopWifiSync');

  @override
  Future<void> setVolume(String type, int level) =>
      _maybeFail('setVolume:$type:$level');

  @override
  Stream<GlassesLabEvent> events() => eventController.stream;
}

void main() {
  late _FakeBridge bridge;
  late GlassesLabController controller;

  setUp(() {
    bridge = _FakeBridge();
    controller = GlassesLabController(bridge);
  });

  tearDown(() {
    controller.dispose();
    bridge.eventController.close();
  });

  Future<void> pump() => Future<void>.delayed(Duration.zero);

  test('loads bridge info on construction', () async {
    await pump();
    expect(controller.bridgeImplementation, 'stub');
    expect(controller.sdkVersion, 'sim-1.0');
    expect(controller.bridgeAvailable, isTrue);
  });

  test('scan populates the device list and clears the busy flag', () async {
    await controller.startScan();
    expect(controller.scanning, isFalse);
    expect(controller.devices, hasLength(1));
    expect(controller.devices.first.name, 'L801-TEST');
  });

  test('connect goes connecting → connected via the event stream', () async {
    await controller.connect('AA:BB');
    expect(controller.connectionState, 'connecting');
    bridge.emit('connectionState', {'state': 'connected', 'mac': 'AA:BB'});
    await pump();
    expect(controller.connectionState, 'connected');
    expect(controller.connectedMac, 'AA:BB');
  });

  test('disconnected event clears the connected MAC', () async {
    await controller.connect('AA:BB');
    bridge.emit('connectionState', {'state': 'connected'});
    bridge.emit('connectionState', {'state': 'disconnected'});
    await pump();
    expect(controller.connectionState, 'disconnected');
    expect(controller.connectedMac, isNull);
  });

  test('battery / wear / deviceInfo events update state', () async {
    bridge.emit('battery', {'pct': 42, 'charging': true});
    bridge.emit('wearState', {'worn': true});
    bridge.emit('deviceInfo', {'btFirmware': '1.0.2'});
    await pump();
    expect(controller.batteryPct, 42);
    expect(controller.charging, isTrue);
    expect(controller.worn, isTrue);
    expect(controller.deviceInfo['btFirmware'], '1.0.2');
  });

  test('thumbnail event stores latency and caps the strip at 5', () async {
    for (var i = 0; i < 7; i++) {
      bridge.emit('thumbnail', {
        'jpeg': Uint8List.fromList([1, 2, 3]),
        'elapsedMs': 1000 + i,
      });
    }
    await pump();
    expect(controller.thumbnails, hasLength(GlassesLabController.maxThumbnails));
    // Newest first.
    expect(controller.thumbnails.first.elapsedMs, 1006);
    expect(controller.photoInFlight, isFalse);
  });

  test('pcm chunks count without flooding the console', () async {
    for (var i = 0; i < 100; i++) {
      bridge.emit('pcmChunk', {'bytes': 320, 'sampleRate': 16000});
    }
    await pump();
    expect(controller.pcmChunks, 100);
    expect(controller.pcmSampleRate, 16000);
    final pcmLogged =
        controller.events.where((e) => e.type == 'pcmChunk').length;
    expect(pcmLogged, 2); // 1-in-50 sampling: chunks #1 and #51.
  });

  test('event log is capped at maxEvents', () async {
    for (var i = 0; i < GlassesLabController.maxEvents + 50; i++) {
      bridge.emit('deviceEvent', {'hex': '$i'});
    }
    await pump();
    expect(controller.events.length, GlassesLabController.maxEvents);
    expect(controller.events.last.data['hex'],
        '${GlassesLabController.maxEvents + 49}');
  });

  test('a failing bridge call degrades to an error entry, never throws',
      () async {
    bridge.failNext = true;
    await controller.startScan(); // must not throw
    expect(controller.scanning, isFalse);
    expect(controller.lastError, contains('scan'));
    expect(controller.bridgeImplementation, 'unavailable');
    expect(controller.events.last.type, 'error');
  });

  test('denied BLE permissions block the scan and raise the banner flag',
      () async {
    final denied = GlassesLabController(
      bridge,
      ensureBlePermissions: () async => false,
    );
    await denied.startScan();
    expect(denied.blePermissionDenied, isTrue);
    expect(denied.scanning, isFalse);
    expect(bridge.calls.where((c) => c == 'scan'), isEmpty);
    expect(denied.events.last.type, 'error');
    denied.dispose();
  });

  test('granted BLE permissions clear a previous denial and scan runs',
      () async {
    var granted = false;
    final flaky = GlassesLabController(
      bridge,
      ensureBlePermissions: () async => granted,
    );
    await flaky.startScan();
    expect(flaky.blePermissionDenied, isTrue);
    granted = true;
    await flaky.startScan();
    expect(flaky.blePermissionDenied, isFalse);
    expect(flaky.devices, hasLength(1));
    flaky.dispose();
  });

  test('denied WiFi permissions block the sync, bridge never called',
      () async {
    final denied = GlassesLabController(
      bridge,
      ensureWifiPermissions: () async => false,
    );
    await denied.startWifiSync();
    expect(denied.syncing, isFalse);
    expect(bridge.calls.where((c) => c == 'startWifiSync'), isEmpty);
    expect(denied.events.last.type, 'error');
    denied.dispose();
  });

  test('bridgeInfo lastMac seeds the device list for instant connect',
      () async {
    final remembering = _FakeBridge()
      ..bridgeInfoExtra = {'lastMac': 'C0:97', 'lastName': 'L802_2B1D'};
    final c = GlassesLabController(remembering);
    await pump();
    expect(c.devices, hasLength(1));
    expect(c.devices.first.mac, 'C0:97');
    expect(c.devices.first.name, 'L802_2B1D (saved)');
    c.dispose();
    await remembering.eventController.close();
  });

  test('mediaCount event fills the glasses-memory counters', () async {
    bridge.emit('mediaCount', {'img': 6, 'vid': 2, 'rec': 1});
    await pump();
    expect(controller.mediaImg, 6);
    expect(controller.mediaVid, 2);
    expect(controller.mediaRec, 1);
    expect(controller.mediaTotal, 9);
    bridge.emit('mediaCount', {'img': 0, 'vid': 0, 'rec': 0});
    await pump();
    expect(controller.mediaTotal, 0);
  });

  test('sync progress updates and completes', () async {
    await controller.startWifiSync();
    expect(controller.syncing, isTrue);
    bridge.emit('syncProgress',
        {'file': 'a.jpg', 'pct': 40, 'speedKbps': 400.0});
    bridge.emit('syncProgress',
        {'file': 'a.jpg', 'pct': 100, 'speedKbps': 410.0});
    await pump();
    expect(controller.syncPct, 100);
    expect(controller.syncing, isFalse);
  });
}
