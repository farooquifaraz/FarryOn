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

  /// Whether both permissions are already granted (no prompt shown).
  Future<bool> hasMicAndCamera() async {
    final mic = await Permission.microphone.status;
    final cam = await Permission.camera.status;
    return mic.isGranted && cam.isGranted;
  }

  /// Open the OS app-settings page (for the permanently-denied case).
  Future<void> openSettings() => openAppSettings();
}
