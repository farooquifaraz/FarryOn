import 'dart:convert';

import 'package:farryon/core/config.dart';
import 'package:farryon/data/data_api.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// A 401 means the session is over — not that the backend is unwell.
///
/// Regression test for a real one: an admin suspended an account while the phone
/// was signed in, and the Notes screen answered "Couldn't load — check the
/// backend." over a backend that was perfectly healthy, with a Retry button that
/// could never work. The 401 was invisible: `jsonDecode(body) as List` threw a
/// cast error on the error envelope, which looked like any other failure.
void main() {
  const config =
      AppConfig(host: 'x', port: 8000, secure: false, authToken: 'tok');

  DataApi apiThat(
    http.Response Function(http.Request) respond, {
    void Function()? onExpired,
  }) =>
      DataApi(
        config,
        client: MockClient((req) async => respond(req)),
        onSessionExpired: onExpired,
      );

  group('401', () {
    test('reads throw SessionExpiredException, not a cast error', () async {
      final api = apiThat((_) => http.Response(
            jsonEncode({
              'success': false,
              'error': {'code': 'UNAUTHENTICATED', 'message': 'Sign in required.'}
            }),
            401,
          ));

      expect(api.notes(), throwsA(isA<SessionExpiredException>()));
      expect(api.tasks(), throwsA(isA<SessionExpiredException>()));
    });

    test('writes throw too, instead of silently doing nothing', () async {
      // These ignored the status entirely, so a suspended user could tap Delete
      // and watch the row vanish optimistically while the server refused.
      final api = apiThat((_) => http.Response('{}', 401));

      expect(api.deleteNote(1), throwsA(isA<SessionExpiredException>()));
      expect(api.deleteTask(1), throwsA(isA<SessionExpiredException>()));
      expect(api.setTaskDone(1, true), throwsA(isA<SessionExpiredException>()));
    });

    test('fires onSessionExpired once, so the app can sign out', () async {
      var signOuts = 0;
      final api = apiThat(
        (_) => http.Response('{}', 401),
        onExpired: () => signOuts++,
      );

      await expectLater(api.notes(), throwsA(isA<SessionExpiredException>()));
      expect(signOuts, 1);
    });
  });

  group('everything else is left alone', () {
    test('a 200 still parses', () async {
      final api = apiThat((_) => http.Response(
            jsonEncode([
              {'id': 1, 'text': 'hi', 'createdAt': '2026-07-16T00:00:00Z'}
            ]),
            200,
          ));

      final notes = await api.notes();
      expect(notes.single.text, 'hi');
    });

    test('a 500 does NOT sign the user out', () async {
      // The distinction that matters: a broken backend is not a dead session.
      // Signing out on a 500 would log people out over a blip.
      var signOuts = 0;
      final api = apiThat(
        (_) => http.Response('boom', 500),
        onExpired: () => signOuts++,
      );

      await expectLater(api.notes(), throwsA(isNot(isA<SessionExpiredException>())));
      expect(signOuts, 0);
    });

    test('a 404 from delete throws NotFound, and does NOT sign out', () async {
      // 404 is what the backend answers for someone else's row — a scoping
      // refusal, not an auth failure (see test_data_scoping.py). It's also what
      // a row deleted on another device looks like, which is why it's its own
      // exception: the outbox treats it as "already done" rather than retrying.
      var signOuts = 0;
      final api = apiThat(
        (_) => http.Response('{"deleted":false}', 404),
        onExpired: () => signOuts++,
      );

      await expectLater(api.deleteNote(99), throwsA(isA<NotFoundException>()));
      expect(signOuts, 0);
    });
  });
}
