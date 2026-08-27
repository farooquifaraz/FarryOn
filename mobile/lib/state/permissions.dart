import 'package:permission_handler/permission_handler.dart';

import '../core/logger.dart';

/// Outcome of a permission request, with enough nuance for the UI to react.
enum PermissionOutcome {
  /// Both mic and camera are usable.
  granted,

  /// At least one was denied (can ask again).
  denied,

  /// At least one was permanently denied — must open Settings.
  permanentlyDenied,
}

/// Requests and reports the mic + camera permissions FarryOn needs.
///
/// Kept tiny and side-effect-free beyond the OS prompt so the controller can
/// drive the UX (show rationale, route to Settings) from the [PermissionOutcome].
class PermissionsService {
  static final _log = Logger('Permissions');

  /// Request microphone and camera permissions together.
  Future<PermissionOutcome> requestMicAndCamera() async {
    final statuses = await [
      Permission.microphone,
      Permission.camera,
    ].request();

    final mic = statuses[Permission.microphone] ?? PermissionStatus.denied;
    final cam = statuses[Permission.camera] ?? PermissionStatus.denied;
    _log.info('permissions mic=$mic camera=$cam');

    if (mic.isGranted && cam.isGranted) {
      return PermissionOutcome.granted;
    }
    if (mic.isPermanentlyDenied || cam.isPermanentlyDenied) {
      return PermissionOutcome.permanentlyDenied;
    }
    return PermissionOutcome.denied;
  }

  /// Request the microphone alone.
  ///
  /// Live translation never opens the camera, and asking for it anyway would
  /// prompt the user for access to something the feature cannot use — the kind
  /// of request that teaches people to deny permissions.
  Future<PermissionOutcome> requestMicrophone() async {
    final mic = await Permission.microphone.request();
    _log.info('permissions mic=$mic (mic only)');
    if (mic.isGranted) return PermissionOutcome.granted;
    if (mic.isPermanentlyDenied) return PermissionOutcome.permanentlyDenied;
    return PermissionOutcome.denied;
  }

  /// Request the Nearby-Wi-Fi-devices permission (Android 13+).
  ///
  /// Wi-Fi Direct — how the glasses hand their photos/videos over — is gated
  /// behind NEARBY_WIFI_DEVICES since API 33. Without the grant the vendor
  /// SDK's P2P calls fail SILENTLY and a sync sits at "0%" forever
  /// (root-caused on-device 2026-08-28, after the S23 Ultra's Android 16
  /// upgrade left it ungranted). On older Android the plugin reports granted
  /// without a prompt, so calling this is always safe.
  Future<PermissionOutcome> requestNearbyWifi() async {
    final nearby = await Permission.nearbyWifiDevices.request();
    _log.info('permissions nearbyWifiDevices=$nearby');
    if (nearby.isGranted) return PermissionOutcome.granted;
    if (nearby.isPermanentlyDenied) return PermissionOutcome.permanentlyDenied;
    return PermissionOutcome.denied;
  }

  /// Whether Nearby-Wi-Fi is already granted (no prompt shown).
  Future<bool> hasNearbyWifi() async {
    final s = await Permission.nearbyWifiDevices.status;
    return s.isGranted;
  }

  /// Whether both permissions are already granted (no prompt shown).
  Future<bool> hasMicAndCamera() async {
    final mic = await Permission.microphone.status;
    final cam = await Permission.camera.status;
    return mic.isGranted && cam.isGranted;
  }

  /// Open the OS app-settings page (for the permanently-denied case).
  Future<void> openSettings() => openAppSettings();
}
