import 'dart:convert';

import 'package:farryon/core/config.dart';
import 'package:farryon/data/auth_api.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _config = AppConfig(host: 'localhost', port: 8000, secure: false);

/// Serves canned responses keyed by request path, so a test states only the
/// contract it cares about.
AuthApi _apiFor(Map<String, http.Response> byPath) {
  return AuthApi(
    _config,
    client: MockClient((req) async {
      final res = byPath[req.url.path];
      if (res == null) {
        fail('unexpected request to ${req.url.path}');
      }
      return res;
    }),
  );
}

http.Response _json(Object body, [int status = 200]) =>
    http.Response(jsonEncode(body), status,
        headers: {'content-type': 'application/json'});

void main() {
  group('login', () {
    test('returns tokens on success', () async {
      final api = _apiFor({
        '/api/v1/auth/login': _json({
          'success': true,
          'data': {'access_token': 'a1', 'refresh_token': 'r1'},
        }),
      });
      final result = await api.login(email: 'a@b.c', password: 'pw');
      expect(result.ok, isTrue);
      expect(result.tokens?.accessToken, 'a1');
      expect(result.tokens?.refreshToken, 'r1');
      expect(result.needsTwoFactor, isFalse);
    });

    test('surfaces the 2FA challenge instead of tokens', () async {
      final api = _apiFor({
        '/api/v1/auth/login': _json({
          'success': true,
          'data': {'two_factor_required': true, 'pending_token': 'p1'},
        }),
      });
      final result = await api.login(email: 'a@b.c', password: 'pw');
      expect(result.needsTwoFactor, isTrue);
      expect(result.pendingToken, 'p1');
      expect(result.tokens, isNull);
    });

    test('carries the server error message, never throws', () async {
      final api = _apiFor({
        '/api/v1/auth/login': _json({
          'success': false,
          'error': {'code': 'INVALID_CREDENTIALS', 'message': 'Incorrect email or password.'},
        }, 401),
      });
      final result = await api.login(email: 'a@b.c', password: 'nope');
      expect(result.ok, isFalse);
      expect(result.message, 'Incorrect email or password.');
    });

    test('a non-JSON error body folds into a human message', () async {
      final api = AuthApi(_config,
          client: MockClient((_) async => http.Response('<html>502</html>', 502)));
      final result = await api.login(email: 'a@b.c', password: 'pw');
      expect(result.ok, isFalse);
      expect(result.message, isNotEmpty);
    });
  });

  group('register', () {
    test('chains into login and returns its tokens', () async {
      final api = _apiFor({
        '/api/v1/auth/register': _json({
          'success': true,
          'data': {'id': 1, 'email': 'a@b.c'},
        }),
        '/api/v1/auth/login': _json({
          'success': true,
          'data': {'access_token': 'a1', 'refresh_token': 'r1'},
        }),
      });
      final result = await api.register(email: 'a@b.c', password: 'pw');
      expect(result.tokens?.accessToken, 'a1');
      expect(result.accountCreated, isFalse);
    });

    test(
        'a failed auto sign-in reports accountCreated, NOT a failed sign-up '
        '(the account exists — the user must be sent to sign-in)', () async {
      final api = _apiFor({
        '/api/v1/auth/register': _json({
          'success': true,
          'data': {'id': 1, 'email': 'a@b.c'},
        }),
        // e.g. the email was rate-limited by earlier failed logins.
        '/api/v1/auth/login': _json({
          'success': false,
          'error': {
            'code': 'TOO_MANY_ATTEMPTS',
            'message': 'Too many failed attempts. Try again later.',
          },
        }, 429),
      });
      final result = await api.register(email: 'a@b.c', password: 'pw');
      expect(result.accountCreated, isTrue);
      expect(result.ok, isTrue, reason: 'the account really was created');
      expect(result.tokens, isNull);
      expect(result.message, contains('Too many failed attempts'));
    });

    test('a rejected registration is a plain failure', () async {
      final api = _apiFor({
        '/api/v1/auth/register': _json({
          'success': false,
          'error': {'code': 'EMAIL_TAKEN', 'message': 'An account with this email already exists.'},
        }, 409),
      });
      final result = await api.register(email: 'a@b.c', password: 'pw');
      expect(result.ok, isFalse);
      expect(result.accountCreated, isFalse);
      expect(result.message, contains('already exists'));
    });
  });

  group('refresh', () {
    test('rotates on success', () async {
      final api = _apiFor({
        '/api/v1/auth/refresh': _json({
          'success': true,
          'data': {'access_token': 'a2', 'refresh_token': 'r2'},
        }),
      });
      final outcome = await api.refresh('r1');
      expect(outcome.tokens?.accessToken, 'a2');
      expect(outcome.invalid, isFalse);
    });

    test('401 is the ONLY sign-out signal', () async {
      final api = _apiFor({
        '/api/v1/auth/refresh': _json({
          'success': false,
          'error': {'code': 'TOKEN_REUSE_DETECTED', 'message': 'Sign in again.'},
        }, 401),
      });
      final outcome = await api.refresh('r1');
      expect(outcome.invalid, isTrue);
    });

    test('a 500 is unreachable, not invalid — a backend hiccup must not sign '
        'every user out', () async {
      final api = _apiFor({
        '/api/v1/auth/refresh': _json({'detail': 'Internal Server Error'}, 500),
      });
      final outcome = await api.refresh('r1');
      expect(outcome.invalid, isFalse);
      expect(outcome.unreachable, isTrue);
    });

    test('no network is unreachable, not invalid', () async {
      final api = AuthApi(_config,
          client: MockClient((_) async => throw const SocketishError()));
      final outcome = await api.refresh('r1');
      expect(outcome.invalid, isFalse);
      expect(outcome.unreachable, isTrue);
    });
  });
}

/// Stand-in for a transport failure (MockClient can throw anything; AuthApi
/// must treat every throw as "couldn't reach the server").
class SocketishError implements Exception {
  const SocketishError();
}
