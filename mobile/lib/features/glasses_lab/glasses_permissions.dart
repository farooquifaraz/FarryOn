import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:permission_handler/permission_handler.dart';

/// Requests the Bluetooth runtime permissions a BLE scan needs, right when
/// the user taps Scan. Returns true when scanning may proceed.
///
/// Android 12+ prompts for BLUETOOTH_SCAN / BLUETOOTH_CONNECT ("Nearby
/// devices"); on Android 11-and-below permission_handler falls back to the
/// legacy location requirement declared in the manifest. Non-Android
/// platforms (and the host VM under `flutter test`) have nothing to ask.
Future<bool> requestGlassesBlePermissions() async {
  if (kIsWeb || !Platform.isAndroid) return true;
  final statuses = await [
    Permission.bluetoothScan,
    Permission.bluetoothConnect,
  ].request();
  return statuses.values.every((s) => s.isGranted);
}
