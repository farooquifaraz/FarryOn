import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:timezone/data/latest_all.dart' as tzdata;
import 'package:timezone/timezone.dart' as tz;

import 'logger.dart';

/// Schedules and cancels local reminder notifications so a task's due time
/// actually fires on the phone — even when the app is closed.
///
/// The notification id is the backend task id, so updating/completing/deleting
/// a task can reschedule or cancel its exact reminder.
class Notifications {
  Notifications._();

  static final _log = Logger('Notifications');
  static final _plugin = FlutterLocalNotificationsPlugin();
  static bool _ready = false;

  static const _channelId = 'farryon_reminders';

  static const _details = NotificationDetails(
    android: AndroidNotificationDetails(
      _channelId,
      'Reminders',
      channelDescription: 'FarryOn task reminders',
      importance: Importance.max,
      priority: Priority.high,
    ),
  );

  /// Lazily initialise the plugin, timezone DB, channel, and permissions. Safe
  /// to call repeatedly.
  static Future<void> init() async {
    if (_ready) return;
    try {
      tzdata.initializeTimeZones();
      const android = AndroidInitializationSettings('@mipmap/ic_launcher');
      await _plugin.initialize(
        const InitializationSettings(android: android),
      );
      final impl = _plugin
          .resolvePlatformSpecificImplementation<
              AndroidFlutterLocalNotificationsPlugin>();
      await impl?.createNotificationChannel(
        const AndroidNotificationChannel(
          _channelId,
          'Reminders',
          description: 'FarryOn task reminders',
          importance: Importance.max,
        ),
      );
      await impl?.requestNotificationsPermission();
      await impl?.requestExactAlarmsPermission();
      _ready = true;
    } catch (e) {
      _log.warn('notifications init failed: $e');
    }
  }

  /// Schedule (or replace) a reminder with [id] to fire at the absolute instant
  /// [when]. No-op for past times or invalid input.
  static Future<void> schedule({
    required int id,
    required String body,
    required DateTime when,
  }) async {
    await init();
    if (!_ready) return;
    if (when.isBefore(DateTime.now())) {
      _log.debug('skip reminder $id — time is in the past');
      return;
    }
    try {
      // `when` is an absolute instant; expressing it in UTC keeps the fire time
      // correct regardless of the device's configured timezone.
      final at = tz.TZDateTime.from(when, tz.UTC);
      await _plugin.zonedSchedule(
        id,
        'Reminder',
        body,
        at,
        _details,
        androidScheduleMode: AndroidScheduleMode.exactAllowWhileIdle,
        uiLocalNotificationDateInterpretation:
            UILocalNotificationDateInterpretation.absoluteTime,
      );
      _log.info('reminder $id scheduled for $when');
    } catch (e) {
      _log.warn('schedule reminder $id failed: $e');
    }
  }

  static Future<void> cancel(int id) async {
    await init();
    if (!_ready) return;
    try {
      await _plugin.cancel(id);
    } catch (e) {
      _log.warn('cancel reminder $id failed: $e');
    }
  }
}
