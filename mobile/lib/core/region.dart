import 'package:flutter_timezone/flutter_timezone.dart';

import 'config.dart';

/// Where the phone is right now, as far as its clock knows.
///
/// The backend offers a regional price list (India in rupees, bought
/// outright for the period; the UAE in dirhams, renewing) to requests whose
/// `X-Timezone` header names an Indian zone or `Asia/Dubai` — by Faraz's
/// choice the rule is "where the person is now", not their SIM or card. The device's IANA zone (`Asia/Kolkata`) is what
/// the platform reports; it is read once at start-up and sent by [DataApi]
/// with every billing call. If it cannot be read, no header is sent and the
/// backend falls back to the global list — never a wrong regional one.
class DeviceRegion {
  DeviceRegion._();

  /// Read the device zone into [AppConfig.deviceTimezone]. Never throws.
  static Future<void> init() async {
    try {
      final zone = await FlutterTimezone.getLocalTimezone();
      AppConfig.deviceTimezone = zone.identifier;
    } catch (_) {
      AppConfig.deviceTimezone = '';
    }
  }
}
