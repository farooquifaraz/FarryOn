import 'dart:convert';
import 'dart:typed_data';

import 'package:farryon/core/config.dart';
import 'package:farryon/data/finder_api.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// `/detect` is the one backend call that carried no session token: the
/// backend could not meter the scan against a plan, and a backend that
/// requires sign-in would refuse it. The Finder now sends the same bearer
/// header the Notes/Tasks client does, and tells a spent budget (429) and a
/// dead session (401) apart from a generic failure.
void main() {
  final okEnvelope = jsonEncode({
    'ok': true,
    'mode': 'auto',
    'result': {'answer': 'Burj Khalifa'},
  });

  FinderApi apiWith({
    String? token,
    required http.Response Function(http.Request) respond,
    void Function()? onExpired,
  }) =>
      FinderApi(
        AppConfig(host: 'x', port: 8000, secure: false, authToken: token),
        client: MockClient((req) async => respond(req)),
        onSessionExpired: onExpired,
      );

  test('sends the bearer token when the app has one', () async {
    http.Request? seen;
    final api = apiWith(
      token: 'tok',
      respond: (req) {
        seen = req;
        return http.Response(okEnvelope, 200);
      },
    );
    await api.detect(imageBytes: Uint8List.fromList([1, 2, 3]));
    expect(seen!.headers['Authorization'], 'Bearer tok');
    expect(seen!.headers['Content-Type'], startsWith('application/json'));
    expect(seen!.url.path, '/detect');
  });

  test('sends no Authorization header when there is no token', () async {
    http.Request? seen;
    final api = apiWith(
      token: null,
      respond: (req) {
        seen = req;
        return http.Response(okEnvelope, 200);
      },
    );
    await api.detect(imageBytes: Uint8List.fromList([1]));
    expect(seen!.headers.containsKey('Authorization'), isFalse);
  });

  test('a 401 is a dead session: says so and signs out, no generic error', () async {
    var expired = 0;
    final api = apiWith(
      token: 'tok',
      respond: (_) => http.Response('{"detail":"Sign in required."}', 401),
      onExpired: () => expired++,
    );
    await expectLater(
      api.detect(imageBytes: Uint8List.fromList([1])),
      throwsA(isA<FinderException>()
          .having((e) => e.message, 'message', contains('sign in'))),
    );
    expect(expired, 1);
  });

  test('a 429 carries the server\'s own quota message', () async {
    final api = apiWith(
      token: 'tok',
      respond: (_) => http.Response(
        jsonEncode({
          'ok': false,
          'status': 'quota_exceeded',
          'message': "You've used your trial image scans on the free plan.",
        }),
        429,
      ),
    );
    await expectLater(
      api.detect(imageBytes: Uint8List.fromList([1])),
      throwsA(isA<FinderException>()
          .having((e) => e.message, 'message', contains('image scans'))),
    );
  });

  test('other failures still read as a server error with the status', () async {
    final api = apiWith(token: 'tok', respond: (_) => http.Response('x', 500));
    await expectLater(
      api.detect(imageBytes: Uint8List.fromList([1])),
      throwsA(isA<FinderException>()
          .having((e) => e.message, 'message', contains('500'))),
    );
  });
}
