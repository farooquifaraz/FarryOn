import 'package:farryon/core/notifications.dart';
import 'package:farryon/features/live/widgets/transcript_view.dart';
import 'package:farryon/state/live_state.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// A reminder that will never fire must say so.
///
/// Device-proven failure on 2026-07-19 (TEST_PLAN D10). With notifications
/// denied, "Remind me in 3 minutes to drink water" produced: a task row in the
/// database, an empty `dumpsys alarm`, an empty scheduled-notification list —
/// and Farry answering "OK. I've set a reminder for 'drink water' in 3
/// minutes." Nothing anywhere said otherwise.
///
/// The cause was small and worth naming: `Notifications.init()` *requested* the
/// permission and never read the reply, then set `_ready = true` regardless. So
/// the app believed it could schedule, `zonedSchedule` quietly did nothing, and
/// the user was left waiting for an alarm that did not exist.
///
/// This is the same shape as the two R8 reminder bugs before it: the reminder
/// path fails in silence unless something is watching.
void main() {
  group('what the user is told', () {
    test('notifications off: the notice says it will not fire', () {
      final notice = Notifications.noticeFor(ReminderOutcome.notificationsOff);

      expect(notice, isNotNull);
      expect(notice, contains("won't fire"),
          reason: 'the consequence has to be in the sentence');
      expect(notice!.toLowerCase(), contains('notification'));
      expect(notice.toLowerCase(), contains('settings'),
          reason: 'and how to fix it');
    });

    test('a scheduled reminder says nothing', () {
      // Silence is the whole point when it worked. A notice on the happy path
      // is noise, and noise is how the real notice gets ignored.
      expect(Notifications.noticeFor(ReminderOutcome.scheduled), isNull);
    });

    test('a deliberately-dropped stale reminder says nothing', () {
      // >6h in the past is dropped on purpose; the user did not just ask for it.
      expect(Notifications.noticeFor(ReminderOutcome.tooOld), isNull);
    });

    test('a failure says so without blaming the user', () {
      final notice = Notifications.noticeFor(ReminderOutcome.failed);

      expect(notice, isNotNull);
      expect(notice!.toLowerCase(), isNot(contains('permission')),
          reason: 'this one is not the user having refused anything');
    });

    test('only "scheduled" counts as willFire', () {
      // The caller branches on this. If any non-scheduled outcome reported
      // true, the notice would be skipped for the exact case it exists for.
      expect(ReminderOutcome.scheduled.willFire, isTrue);
      for (final o in [
        ReminderOutcome.notificationsOff,
        ReminderOutcome.tooOld,
        ReminderOutcome.failed,
      ]) {
        expect(o.willFire, isFalse, reason: '$o must not claim it will fire');
      }
    });
  });

  group('how it looks', () {
    Future<void> pump(WidgetTester tester, List<TranscriptEntry> entries) =>
        tester.pumpWidget(MaterialApp(
          home: Scaffold(body: TranscriptView(entries: entries)),
        ));

    testWidgets('a notice is not dressed as Farry', (tester) async {
      // She said the reminder *was* set. Putting the contradiction in her voice
      // reads as her changing her mind, not as the phone reporting something
      // she cannot see.
      await pump(tester, const [
        TranscriptEntry(role: 'assistant', text: "OK. I've set a reminder.", isFinal: true),
        TranscriptEntry(role: 'notice', text: "This reminder won't fire.", isFinal: true),
      ]);

      expect(find.text('Farry'), findsOneWidget,
          reason: 'exactly one line is hers — the notice is not');
      expect(find.text("This reminder won't fire."), findsOneWidget);
      expect(find.byIcon(Icons.notifications_off_rounded), findsOneWidget);
    });

    testWidgets('ordinary lines are untouched', (tester) async {
      await pump(tester, const [
        TranscriptEntry(role: 'user', text: 'Remind me to drink water', isFinal: true),
        TranscriptEntry(role: 'assistant', text: 'Done.', isFinal: true),
      ]);

      expect(find.text('You'), findsOneWidget);
      expect(find.text('Farry'), findsOneWidget);
      expect(find.byIcon(Icons.notifications_off_rounded), findsNothing);
    });
  });

  group('the entry model', () {
    test('a notice is neither the user nor an ordinary line', () {
      const notice = TranscriptEntry(role: 'notice', text: 'x', isFinal: true);

      expect(notice.isNotice, isTrue);
      expect(notice.isUser, isFalse);
    });

    test('assistant and user lines are not notices', () {
      const user = TranscriptEntry(role: 'user', text: 'x', isFinal: true);
      const farry = TranscriptEntry(role: 'assistant', text: 'x', isFinal: true);

      expect(user.isNotice, isFalse);
      expect(farry.isNotice, isFalse);
    });
  });
}
