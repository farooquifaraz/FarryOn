import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:timezone/data/latest_all.dart' as tzdata;
import 'package:timezone/timezone.dart' as tz;

import 'logger.dart';

/// What actually happened to a reminder, so the caller can say so.
///
/// This exists because of a real failure: with notifications denied, the task
/// was created, nothing was scheduled, and Farry answered "OK. I've set a
/// reminder for 'drink water' in 3 minutes." `dumpsys alarm` had no entry and
/// the plugin's own list was empty. A reminder that quietly never fires is
/// worse than one that fails loudly — the user stops watching for the thing
/// they asked to be reminded of.
enum ReminderOutcome {
  /// On the phone's alarm list. It will fire.
  scheduled,

  /// The user refused notifications. Nothing can fire until they change that.
  notificationsOff,

  /// Long past its time — deliberately dropped, see [Notifications.schedule].
  tooOld,

  /// The plugin refused it. The log has the reason.
  failed;

  bool get willFire => this == ReminderOutcome.scheduled;
}

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
  static Future<ReminderOutcome> schedule({
    required int id,
    required String body,
    required DateTime when,
  }) async {
    await init();
    if (!_ready) return ReminderOutcome.failed;

    // Asked every time, not cached from init(): the user can turn notifications
    // off in Settings long after the app started, and the answer we want is the
    // one that holds now. `init()` used to *request* the permission and never
    // look at the reply — which is how a refused permission still produced a
    // cheerful "reminder set".
    if (!await notificationsAllowed()) {
      _log.warn('reminder $id NOT scheduled — notifications are off');
      return ReminderOutcome.notificationsOff;
    }

    final now = DateTime.now();
    var fireAt = when;
    if (when.isBefore(now)) {
      final lateBy = now.difference(when);
      if (lateBy > const Duration(hours: 6)) {
        _log.debug('skip reminder $id — ${lateBy.inMinutes}min in the past');
        return ReminderOutcome.tooOld;
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
      return ReminderOutcome.scheduled;
    } catch (e) {
      _log.warn('schedule reminder $id failed: $e');
      return ReminderOutcome.failed;
    }
  }

  /// Whether the OS will actually deliver a notification right now.
  ///
  /// Two sources because they can disagree: the plugin reports the app's
  /// notification setting, and `permission_handler` reports POST_NOTIFICATIONS
  /// (Android 13+). Either one being off means nothing reaches the user, so a
  /// reminder only counts as schedulable when both agree.
  static Future<bool> notificationsAllowed() async {
    try {
      final impl = _plugin.resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin>();
      final enabled = await impl?.areNotificationsEnabled() ?? true;
      if (!enabled) return false;
      return await Permission.notification.isGranted;
    } catch (e) {
      // A check that cannot run must not block a reminder — that would trade a
      // silent failure for a silent refusal, which is no better.
      _log.warn('could not read notification permission: $e');
      return true;
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

  /// The line to show a user whose reminder will not fire.
  ///
  /// Plain about the consequence — "won't fire" — rather than naming a
  /// permission, because the person reading it wants to know whether to trust
  /// the reminder, not which Android setting is involved.
  static String? noticeFor(ReminderOutcome outcome) => switch (outcome) {
        ReminderOutcome.scheduled => null,
        ReminderOutcome.notificationsOff =>
          "This reminder won't fire — notifications are turned off for Farry. "
              'Turn them on in Settings and set it again.',
        ReminderOutcome.tooOld => null,
        ReminderOutcome.failed =>
          "This reminder couldn't be set on your phone. Please try again.",
      };

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
