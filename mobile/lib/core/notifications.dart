import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:permission_handler/permission_handler.dart';
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
  // Init is attempted exactly once per process. Without this, a failed init
  // (e.g. a stripped icon resource) re-runs on every schedule()/cancel() and
  // re-fires the permission dialogs each time — which backgrounds the app and
  // churns the camera. One attempt, then we stop asking.
  static bool _attempted = false;

  static const _channelId = 'farryon_reminders';

  static const _details = NotificationDetails(
    android: AndroidNotificationDetails(
      _channelId,
      'Reminders',
      channelDescription: 'Farry task reminders',
      importance: Importance.max,
      priority: Priority.high,
      // White silhouette of the app mark — Android tints the small icon, so a
      // dedicated monochrome icon renders cleanly instead of a white square.
      icon: 'ic_notification',
    ),
  );

  /// Lazily initialise the plugin, timezone DB, channel, and permissions. Safe
  /// to call repeatedly.
  static Future<void> init() async {
    if (_ready || _attempted) return;
    _attempted = true;
    try {
      tzdata.initializeTimeZones();
      const android = AndroidInitializationSettings('ic_notification');
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
          description: 'Farry task reminders',
          importance: Importance.max,
        ),
      );
      await impl?.requestNotificationsPermission();
      await impl?.requestExactAlarmsPermission();
      // Critical on aggressive OEMs (Samsung): without a battery-optimisation
      // exemption the OS delays/blocks the exact alarm, so reminders never fire.
      try {
        await Permission.notification.request();
        await Permission.scheduleExactAlarm.request();
        if (await Permission.ignoreBatteryOptimizations.isDenied) {
          await Permission.ignoreBatteryOptimizations.request();
        }
      } catch (e) {
        _log.warn('battery/alarm permission request failed: $e');
      }
      _ready = true;
    } catch (e) {
      _log.warn('notifications init failed: $e');
    }
  }

  /// Schedule (or replace) a reminder with [id] to fire at the absolute instant
  /// [when]. A reminder that lands slightly in the past — e.g. a relative
  /// "in 2 minutes" resolved against a session clock that has since gone stale —
  /// is fired a few seconds from now instead of being silently dropped, so the
  /// user never loses a reminder. Only clearly-stale times (>6h old) are
  /// skipped.
  static Future<void> schedule({
    required int id,
    required String body,
    required DateTime when,
  }) async {
    await init();
    if (!_ready) return;
    final now = DateTime.now();
    var fireAt = when;
    if (when.isBefore(now)) {
      final lateBy = now.difference(when);
      if (lateBy > const Duration(hours: 6)) {
        _log.debug('skip reminder $id — ${lateBy.inMinutes}min in the past');
        return;
      }
      fireAt = now.add(const Duration(seconds: 5));
      _log.info('reminder $id was ${lateBy.inSeconds}s late; firing in 5s');
    }
    try {
      // `when` is an absolute instant; expressing it in UTC keeps the fire time
      // correct regardless of the device's configured timezone.
      final at = tz.TZDateTime.from(fireAt, tz.UTC);
      await _plugin.zonedSchedule(
        id,
        'Reminder',
        body,
        at,
        _details,
        // `alarmClock` schedules via Android's setAlarmClock() — the same
        // privileged path alarm-clock apps use. Unlike exactAllowWhileIdle, it
        // is NOT deferred by Doze or Battery Saver and ignores the
        // allow-while-idle quota, so the reminder fires at the exact moment even
        // when the phone is asleep, locked, or in power-saving mode.
        androidScheduleMode: AndroidScheduleMode.alarmClock,
        uiLocalNotificationDateInterpretation:
            UILocalNotificationDateInterpretation.absoluteTime,
      );
      _log.info('reminder $id scheduled for $when');
    } catch (e) {
      _log.warn('schedule reminder $id failed: $e');
    }
  }

  /// Fire a self-test reminder [seconds] from now (default 20s). Used by the
  /// "Test reminder" button so the user can verify delivery works on their
  /// phone — even with the screen locked and Battery Saver on. Returns the
  /// absolute time it will fire.
  static Future<DateTime> testReminder({int seconds = 20}) async {
    final when = DateTime.now().add(Duration(seconds: seconds));
    await schedule(
      id: 999000,
      body: 'Test reminder — delivery works! ✅',
      when: when,
    );
    return when;
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
