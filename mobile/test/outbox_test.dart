import 'dart:convert';

import 'package:farryon/core/config.dart';
import 'package:farryon/core/outbox.dart';
import 'package:farryon/core/outbox_sync.dart';
import 'package:farryon/data/data_api.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Changes made offline reach the server later.
///
/// Phase 3 of docs/LOCAL_FIRST_SYNC.md, and smaller than that document planned
/// for: it assumed offline *creates* needing client-generated UUIDs, but the app
/// cannot create a note — adding is voice-only and Farry runs server-side, so
/// creating one already needs the network. Everything queueable acts on a row
/// that already exists with a real server id, which is what makes every
/// operation here idempotent.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    await Outbox.init();
  });

  const config = AppConfig(host: 'x', port: 8000, secure: false, authToken: 't');

  DataApi apiThat(int status, {List<String>? seen}) => DataApi(
        config,
        client: MockClient((req) async {
          seen?.add('${req.method} ${req.url.path}');
          return http.Response(jsonEncode({}), status);
        }),
      );

  DataApi offlineApi() => DataApi(
        config,
        client: MockClient((_) async => throw const SocketExceptionStub()),
      );

  group('queueing', () {
    test('a queued delete survives to be sent later', () async {
      await Outbox.add(1, const OutboxOp(kind: OutboxKind.deleteNote, id: 5));

      final pending = Outbox.pending(1);
      expect(pending, hasLength(1));
      expect(pending.single.kind, OutboxKind.deleteNote);
      expect(pending.single.id, 5);
      expect(pending.single.queuedAt, isNotNull);
    });

    test('toggling the same task repeatedly queues once', () async {
      // Tapping a checkbox on and off for a minute on a train would otherwise
      // queue sixty requests that all say the same thing once the last lands.
      for (final done in [true, false, true, false, true]) {
        await Outbox.add(
          1,
          OutboxOp(kind: OutboxKind.setTaskDone, id: 9, done: done),
        );
      }

      final pending = Outbox.pending(1);
      expect(pending, hasLength(1));
      expect(pending.single.done, isTrue, reason: 'the last tap wins');
    });

    test('different rows and kinds keep their own slots', () async {
      await Outbox.add(1, const OutboxOp(kind: OutboxKind.deleteNote, id: 1));
      await Outbox.add(1, const OutboxOp(kind: OutboxKind.deleteTask, id: 1));
      await Outbox.add(1, const OutboxOp(kind: OutboxKind.setTaskDone, id: 2));

      expect(Outbox.pending(1), hasLength(3));
    });

    test("one phone, two people: queues don't mix", () async {
      await Outbox.add(1, const OutboxOp(kind: OutboxKind.deleteNote, id: 1));

      expect(Outbox.pending(2), isEmpty);
      expect(Outbox.pending(1), hasLength(1));
    });

    test('sign-out drops every queue', () async {
      // A queue of changes belongs to whoever made them. Replaying one user's
      // deletions after another signs in would be indefensible.
      await Outbox.add(1, const OutboxOp(kind: OutboxKind.deleteNote, id: 1));
      await Outbox.add(2, const OutboxOp(kind: OutboxKind.deleteTask, id: 2));

      await Outbox.clear();

      expect(Outbox.pending(1), isEmpty);
      expect(Outbox.pending(2), isEmpty);
    });
  });

  group('draining', () {
    test('sends what was queued and empties the queue', () async {
      await Outbox.add(1, const OutboxOp(kind: OutboxKind.deleteNote, id: 5));
      await Outbox.add(1, const OutboxOp(kind: OutboxKind.deleteTask, id: 6));
      final seen = <String>[];

      final sent = await OutboxSync.drain(apiThat(200, seen: seen), 1);

      expect(sent, 2);
      expect(seen, ['DELETE /notes/5', 'DELETE /tasks/6']);
      expect(Outbox.pending(1), isEmpty);
    });

    test('a 404 counts as done, not as a failure', () async {
      // The row is already gone — deleted on another device, or our request
      // landed and only the reply was lost. Retrying forever would be a queue
      // that never empties.
      await Outbox.add(1, const OutboxOp(kind: OutboxKind.deleteNote, id: 5));

      final sent = await OutboxSync.drain(apiThat(404), 1);

      expect(sent, 1);
      expect(Outbox.pending(1), isEmpty);
    });

    test('still offline: nothing is sent and nothing is lost', () async {
      await Outbox.add(1, const OutboxOp(kind: OutboxKind.deleteNote, id: 5));

      final sent = await OutboxSync.drain(offlineApi(), 1);

      expect(sent, 0);
      expect(Outbox.pending(1), hasLength(1), reason: 'must survive to retry');
    });

    test('a 500 stops the drain and keeps the rest queued', () async {
      // Grinding through forty more requests to learn the server is unwell
      // forty more times wastes battery for no information.
      for (var i = 1; i <= 3; i++) {
        await Outbox.add(1, OutboxOp(kind: OutboxKind.deleteNote, id: i));
      }
      final seen = <String>[];

      await OutboxSync.drain(apiThat(500, seen: seen), 1);

      expect(seen, hasLength(1), reason: 'stopped after the first failure');
      expect(Outbox.pending(1), hasLength(3));
    });

    test('an expired session stops the drain', () async {
      // DataApi is signing the user out; the sign-out clears the queue anyway.
      await Outbox.add(1, const OutboxOp(kind: OutboxKind.deleteNote, id: 5));

      final sent = await OutboxSync.drain(apiThat(401), 1);

      expect(sent, 0);
    });

    test('an empty queue is a no-op', () async {
      final seen = <String>[];
      expect(await OutboxSync.drain(apiThat(200, seen: seen), 1), 0);
      expect(seen, isEmpty);
    });

    test('re-sending after a crash is safe', () async {
      // Every operation is idempotent by construction, so "did that land before
      // we lost the connection?" has the same answer either way: send it again.
      await Outbox.add(1, const OutboxOp(kind: OutboxKind.setTaskDone, id: 9, done: true));
      final seen = <String>[];

      await OutboxSync.drain(apiThat(200, seen: seen), 1);
      await Outbox.add(1, const OutboxOp(kind: OutboxKind.setTaskDone, id: 9, done: true));
      await OutboxSync.drain(apiThat(200, seen: seen), 1);

      expect(seen, hasLength(2));
      expect(Outbox.pending(1), isEmpty);
    });
  });

  test('an unreadable queue is dropped rather than retried forever', () async {
    await (await SharedPreferences.getInstance())
        .setString('outbox.v1.u1', 'not json');

    expect(Outbox.pending(1), isEmpty);
  });
}

/// Stands in for a real socket failure — MockClient can't produce one.
class SocketExceptionStub implements Exception {
  const SocketExceptionStub();
}
