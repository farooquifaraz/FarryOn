import 'package:farryon/core/data_cache.dart';
import 'package:farryon/data/data_api.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// The read half of local-first (docs/LOCAL_FIRST_SYNC.md phase 2): the screens
/// paint from this cache immediately, then refresh behind it.
///
/// The case these exist for is **one phone, two people**. A cache keyed on
/// nothing hands the second person the first person's notes — the same bug the
/// backend was fixed for on 2026-07-15, re-created on the client where no amount
/// of server-side scoping can catch it.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    await DataCache.init();
  });

  List<NoteItem> notes(String text) => [
        NoteItem(id: 1, text: text, createdAt: '2026-07-16T10:00:00Z'),
      ];

  group('one phone, two people', () {
    test("B never sees A's notes", () async {
      await DataCache.saveNotes(1, notes("A's private note"));

      expect(DataCache.notes(2), isNull, reason: 'B has no cache of their own');
      expect(DataCache.notes(1)!.single.text, "A's private note");
    });

    test('signing out drops every account, not just the one leaving', () async {
      // Otherwise a phone that has held two accounts keeps the other one's
      // notes sitting there for whoever picks it up next.
      await DataCache.saveNotes(1, notes("A's note"));
      await DataCache.saveNotes(2, notes("B's note"));

      await DataCache.clear();

      expect(DataCache.notes(1), isNull);
      expect(DataCache.notes(2), isNull);
    });

    test('a signed-out session has its own bucket', () async {
      await DataCache.saveNotes(null, notes('anon note'));
      await DataCache.saveNotes(7, notes('u7 note'));

      expect(DataCache.notes(null)!.single.text, 'anon note');
      expect(DataCache.notes(7)!.single.text, 'u7 note');
    });
  });

  group('round trip', () {
    test('notes survive with their fields intact', () async {
      await DataCache.saveNotes(1, notes('hello'));
      final back = DataCache.notes(1)!.single;

      expect(back.id, 1);
      expect(back.text, 'hello');
      expect(back.createdAt, '2026-07-16T10:00:00Z');
    });

    test('tasks keep done and dueDate — the two fields that matter', () async {
      await DataCache.saveTasks(1, [
        const TaskItem(
          id: 5,
          title: 'dentist',
          done: true,
          dueDate: '2026-07-20T09:00:00Z',
        ),
      ]);
      final back = DataCache.tasks(1)!.single;

      expect(back.id, 5);
      expect(back.title, 'dentist');
      expect(back.done, isTrue, reason: 'a ticked task must not come back open');
      expect(back.dueDate, '2026-07-20T09:00:00Z');
    });

    test('an empty list is remembered as empty, not as "no answer"', () async {
      // These are different: null means "we have never heard from the server",
      // and the screen must keep its spinner rather than claim you have nothing.
      await DataCache.saveNotes(1, []);

      expect(DataCache.notes(1), isEmpty);
      expect(DataCache.notes(1), isNotNull);
    });

    test('nothing cached reads as null', () {
      expect(DataCache.notes(99), isNull);
      expect(DataCache.tasks(99), isNull);
    });

    test('saving replaces rather than appends', () async {
      await DataCache.saveNotes(1, notes('first'));
      await DataCache.saveNotes(1, notes('second'));

      expect(DataCache.notes(1)!.single.text, 'second');
    });
  });

  test('unreadable cache reads as null and clears itself', () async {
    // A cache that can't be parsed is a cache we don't have. Throwing would
    // break the screen this exists to speed up — and leaving the bad bytes
    // there would break it on every launch.
    // Write the junk through the same prefs instance DataCache already holds:
    // init() is `??=`, which is right for the app (one process, one store) but
    // means a fresh setMockInitialValues() would never be picked up here.
    await (await SharedPreferences.getInstance())
        .setString('cache.notes.v1.u1', 'not json');

    expect(DataCache.notes(1), isNull);
    expect(DataCache.notes(1), isNull, reason: 'still null after self-clearing');
  });
}
