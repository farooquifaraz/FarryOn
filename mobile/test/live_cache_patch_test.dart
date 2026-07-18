import 'package:farryon/core/cache_patch.dart';
import 'package:farryon/core/data_cache.dart';
import 'package:farryon/data/data_api.dart';
import 'package:farryon/protocol/messages.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Farry's own notes land in the phone's cache without a refresh.
///
/// Phase 4 of docs/LOCAL_FIRST_SYNC.md. She writes server-side — "yaad rakho,
/// dentist Tuesday" creates the row with the phone uninvolved — so her notes
/// used to appear only after a manual refresh. The `tool_result` was already
/// arriving over the WebSocket; this stops it being thrown away.
///
/// These call the handler directly. Going through a real WebSocket would drag
/// in the audio engine, the camera and the glasses channel to test six lines of
/// list-patching.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    await DataCache.init();
  });

  ToolResultMessage result(String name, Map<String, dynamic> data) =>
      ToolResultMessage(id: 'call-1', name: name, ok: true, result: data);

  NoteItem note(int id, String text) =>
      NoteItem(id: id, text: text, createdAt: '2026-07-16T10:00:00Z');

  TaskItem task(int id, String title, {bool done = false}) =>
      TaskItem(id: id, title: title, done: done);

  group('create', () {
    test('a note Farry saves is at the top of the cache', () async {
      await DataCache.saveNotes(1, [note(1, 'older')]);

      applyToolResultToCache(
        result('create_note', {
          'id': 2,
          'text': 'dentist Tuesday',
          'createdAt': '2026-07-16T12:00:00Z',
        }),
        1,
      );
      await Future<void>.delayed(Duration.zero);

      final cached = DataCache.notes(1)!;
      expect(cached.first.text, 'dentist Tuesday', reason: 'newest first');
      expect(cached.first.createdAt, '2026-07-16T12:00:00Z');
      expect(cached, hasLength(2), reason: 'the older note must survive');
    });

    test('a task keeps its due date and done flag', () async {
      await DataCache.saveTasks(1, []);

      applyToolResultToCache(
        result('create_task', {
          'id': 9,
          'title': 'call the dentist',
          'due_date': '2026-07-20T09:00:00Z',
          'done': false,
          'createdAt': '2026-07-16T12:00:00Z',
        }),
        1,
      );
      await Future<void>.delayed(Duration.zero);

      final t = DataCache.tasks(1)!.single;
      expect(t.title, 'call the dentist');
      expect(t.dueDate, '2026-07-20T09:00:00Z');
      expect(t.done, isFalse);
    });

    test('the same id twice does not duplicate', () async {
      // The WS can redeliver, and a duplicate row on screen looks like a bug in
      // Farry rather than in us.
      await DataCache.saveNotes(1, []);
      final msg = result('create_note', {'id': 5, 'text': 'once'});

      applyToolResultToCache(msg, 1);
      await Future<void>.delayed(Duration.zero);
      applyToolResultToCache(msg, 1);
      await Future<void>.delayed(Duration.zero);

      expect(DataCache.notes(1), hasLength(1));
    });
  });

  group('remove and complete', () {
    test('a deleted note leaves the cache', () async {
      await DataCache.saveNotes(1, [note(1, 'keep'), note(2, 'drop')]);

      applyToolResultToCache(result('delete_note', {'id': 2}), 1);
      await Future<void>.delayed(Duration.zero);

      expect(DataCache.notes(1)!.single.text, 'keep');
    });

    test('a completed task is ticked, not removed', () async {
      await DataCache.saveTasks(1, [task(3, 'dentist')]);

      applyToolResultToCache(
        result('complete_task', {'id': 3, 'title': 'dentist', 'done': true}),
        1,
      );
      await Future<void>.delayed(Duration.zero);

      expect(DataCache.tasks(1)!.single.done, isTrue);
    });
  });

  group('what it deliberately does not do', () {
    test('never seeds a cache that was never fetched', () async {
      // null means "we have never asked the server". Writing one note here
      // would tell the Notes screen "this is everything you have" and hide the
      // rest until something forced a refresh.
      applyToolResultToCache(result('create_note', {'id': 1, 'text': 'hi'}), 1);
      await Future<void>.delayed(Duration.zero);

      expect(DataCache.notes(1), isNull);
    });

    test('ignores a failed tool call', () async {
      await DataCache.saveNotes(1, [note(1, 'keep')]);

      applyToolResultToCache(
        const ToolResultMessage(
          id: 'c',
          name: 'delete_note',
          ok: false,
          result: {'id': 1},
        ),
        1,
      );
      await Future<void>.delayed(Duration.zero);

      expect(DataCache.notes(1), hasLength(1), reason: 'nothing happened');
    });

    test('leaves update_task alone', () async {
      // Its result carries only the fields that changed, so patching from it
      // would blank whatever it didn't mention. The screen's refetch handles it.
      await DataCache.saveTasks(1, [task(4, 'original title')]);

      applyToolResultToCache(
        result('update_task', {'id': 4, 'due_date': '2026-08-01T09:00:00Z'}),
        1,
      );
      await Future<void>.delayed(Duration.zero);

      expect(DataCache.tasks(1)!.single.title, 'original title');
    });

    test("writes to the signed-in user's cache, not someone else's", () async {
      await DataCache.saveNotes(1, []);
      await DataCache.saveNotes(2, []);

      applyToolResultToCache(result('create_note', {'id': 7, 'text': 'mine'}), 2);
      await Future<void>.delayed(Duration.zero);

      expect(DataCache.notes(2), hasLength(1));
      expect(DataCache.notes(1), isEmpty, reason: "user 1's cache is untouched");
    });
  });
}
