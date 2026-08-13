import 'dart:convert';

import 'package:farryon/core/config_store.dart';
import 'package:farryon/core/jwt.dart';
import 'package:farryon/data/auth_api.dart';
import 'package:farryon/state/auth.dart';
import 'package:farryon/state/live_state.dart';
import 'package:farryon/state/providers.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Access tokens live 15 minutes; refresh tokens live 30 days. Until this was
/// fixed, `refresh` was called from exactly one place — cold start — so leaving
/// the app open for a quarter of an hour killed it: the WS handshake started
/// coming back 403, the client reconnected on its backoff forever with the same
/// dead token, and the screen kept saying "Listening…".
///
/// Found on the S23 on 2026-08-11 while testing live translation, but nothing
/// about it is specific to translation — the assistant socket carries the same
/// token and died the same way.
///
/// The fakes here are copied from `auth_restore_test.dart` on purpose: that
/// file pins "sign out only on 401", and renewal must not quietly undo it.
class _FakeApi implements AuthApi {
  _FakeApi(this._outcome);

  final AuthRefreshOutcome Function() _outcome;
  int refreshCalls = 0;

  @override
  Future<AuthRefreshOutcome> refresh(String refreshToken) async {
    refreshCalls++;
    return _outcome();
  }

  @override
  Future<AuthUser?> me(String accessToken) async => null;

  /// Sign-out tells the server to revoke the refresh token; the fake only has
  /// to not throw, since what is under test is what happens on THIS device.
  @override
  Future<void> logout(String refreshToken) async {}

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _FakeLive extends LiveNotifier {
  @override
  LiveSessionState build() => const LiveSessionState();

  @override
  Future<void> disconnect() async {}
}

/// An unsigned token with a real `exp`. The client never verifies the
/// signature — only the server can — so a plausible payload is enough.
String _token(String name, Duration life) {
  String seg(Map<String, Object?> m) =>
      base64Url.encode(utf8.encode(jsonEncode(m))).replaceAll('=', '');
  final exp = DateTime.now().toUtc().add(life).millisecondsSinceEpoch ~/ 1000;
  return '${seg({'alg': 'HS256'})}.'
      '${seg({'sub': '1', 'type': 'access', 'exp': exp, 'name': name})}.sig';
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  final keystore = <String, String>{};
  setUp(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
      const MethodChannel('plugins.it_nomads.com/flutter_secure_storage'),
      (call) async {
        switch (call.method) {
          case 'write':
            keystore[call.arguments['key'] as String] =
                call.arguments['value'] as String;
            return null;
          case 'read':
            return keystore[call.arguments['key'] as String];
          case 'delete':
            keystore.remove(call.arguments['key'] as String);
            return null;
          case 'deleteAll':
            keystore.clear();
            return null;
          case 'readAll':
            return keystore;
          default:
            return null;
        }
      },
    );
    keystore.clear();
  });

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    await ConfigStore.init();
  });

  tearDown(() async => ConfigStore.clearAuthSession());

  Future<ProviderContainer> signedInWith(
    _FakeApi api, {
    required String access,
  }) async {
    await ConfigStore.saveAuthSession(
      access: access,
      refresh: 'stored-refresh',
      email: 'faraz@example.com',
    );
    final container = ProviderContainer(overrides: [
      authApiProvider.overrideWithValue(api),
      liveProvider.overrideWith(_FakeLive.new),
    ]);
    addTearDown(container.dispose);
    container.read(authProvider);
    for (var i = 0; i < 50; i++) {
      await Future<void>.delayed(Duration.zero);
      if (!container.read(authProvider).isRestoring) break;
    }
    return container;
  }

  Future<void> settle() async {
    for (var i = 0; i < 50; i++) {
      await Future<void>.delayed(Duration.zero);
    }
  }

  group('jwtExpiry', () {
    test('reads exp, and treats junk as unknown rather than expired', () {
      final t = _token('a', const Duration(minutes: 15));
      expect(jwtExpiry(t), isNotNull);
      expect(jwtExpiresWithin(t, const Duration(minutes: 3)), isFalse);
      expect(jwtExpiresWithin(t, const Duration(minutes: 20)), isTrue);

      // An unreadable token must not be *reported* as expired: doing so would
      // sign people out over a format we failed to anticipate.
      for (final junk in ['', 'not-a-jwt', 'a.b', 'a.!!!.c']) {
        expect(jwtExpiry(junk), isNull, reason: junk);
        expect(jwtExpiresWithin(junk, const Duration(days: 1)), isFalse,
            reason: junk);
      }
    });
  });

  test('a spent access token is renewed, and the socket gets the new one',
      () async {
    // This is the regression: before the fix nothing renewed mid-run, so the
    // config kept handing the WS a token the server had already rejected.
    //
    // Cold start is deliberately made to fail (app opened with no signal), so
    // the spent token survives into the running app — which is the state that
    // used to be unrecoverable without a restart.
    var call = 0;
    final api = _FakeApi(() {
      call++;
      if (call == 1) return const AuthRefreshOutcome.unreachable();
      return AuthRefreshOutcome.rotated(
        AuthTokens(
          accessToken: _token('fresh', const Duration(minutes: 15)),
          refreshToken: 'refresh-2',
        ),
      );
    });
    final container =
        await signedInWith(api, access: _token('old', const Duration(minutes: 1)));
    final afterRestore = api.refreshCalls; // cold start tried and failed

    await container.read(authProvider.notifier).ensureFreshToken();
    await settle();

    expect(api.refreshCalls, afterRestore + 1,
        reason: 'a token one minute from expiry has to be renewed');
    final token = container.read(configProvider).authToken;
    expect(jwtClaims(token)?['name'], 'fresh',
        reason: 'the live socket must be pointed at the renewed token');
    expect(container.read(authProvider).isSignedIn, isTrue);
  });

  test('a renewal that hangs does not switch renewal off forever', () async {
    // The real failure, second time around. The first fix armed ONE timer for
    // the moment a renewal was due; a request that never returned meant the
    // timer never re-armed and renewal was silently off for the rest of the
    // run. Seen on the S23: a token minted at 14:13 still being sent at 15:06.
    //
    // Now the request has a deadline and a heartbeat re-checks, so one hung
    // call costs one attempt rather than the session.
    var call = 0;
    final api = _FakeApi(() {
      call++;
      // Cold start takes the first; the hung attempt under test is the second.
      if (call <= 2) return const AuthRefreshOutcome.unreachable();
      return AuthRefreshOutcome.rotated(
        AuthTokens(
          accessToken: _token('fresh', const Duration(minutes: 15)),
          refreshToken: 'refresh-2',
        ),
      );
    });
    final container =
        await signedInWith(api, access: _token('old', const Duration(minutes: 1)));
    final afterRestore = api.refreshCalls;

    // First attempt fails the way a hung request now resolves.
    await container.read(authProvider.notifier).ensureFreshToken();
    await settle();
    expect(api.refreshCalls, afterRestore + 1);
    expect(container.read(authProvider).isSignedIn, isTrue);

    // The second attempt must still be possible — this is the whole point.
    await container.read(authProvider.notifier).ensureFreshToken();
    await settle();
    expect(api.refreshCalls, afterRestore + 2,
        reason: 'one failed renewal disabled every later one');
    expect(jwtClaims(container.read(configProvider).authToken)?['name'], 'fresh');
  });

  test('a token with life left is left alone', () async {
    final api = _FakeApi(() => const AuthRefreshOutcome.unreachable());
    final container = await signedInWith(
      api,
      access: _token('healthy', const Duration(minutes: 14)),
    );
    final afterRestore = api.refreshCalls;

    await container.read(authProvider.notifier).ensureFreshToken();
    await settle();

    expect(api.refreshCalls, afterRestore,
        reason: 'renewing a healthy token is pure noise on the network');
  });

  test('an unreachable server keeps the session — it is not a sign-out',
      () async {
    // The whole point of the 30-day refresh token: a failed renewal on a train
    // must not log anyone out. Same rule auth_restore_test pins for cold start.
    final api = _FakeApi(() => const AuthRefreshOutcome.unreachable());
    final container = await signedInWith(
      api,
      access: _token('old', const Duration(seconds: 30)),
    );

    await container.read(authProvider.notifier).ensureFreshToken();
    await settle();

    expect(container.read(authProvider).isSignedIn, isTrue);
  });

  test('a refresh token the server disowns does sign out', () async {
    // The opposite failure: staying "signed in" on tokens the server rejects is
    // exactly the endless-silent-reconnect state this whole fix exists to end.
    final api = _FakeApi(() => const AuthRefreshOutcome.invalid());
    final container = await signedInWith(
      api,
      access: _token('old', const Duration(seconds: 30)),
    );

    await container.read(authProvider.notifier).ensureFreshToken();
    await settle();

    expect(container.read(authProvider).isSignedIn, isFalse);
  });

  test('signing out disarms renewal, so nothing writes the session back',
      () async {
    final api = _FakeApi(() => AuthRefreshOutcome.rotated(
          AuthTokens(
            accessToken: _token('ghost', const Duration(minutes: 15)),
            refreshToken: 'refresh-2',
          ),
        ));
    final container = await signedInWith(
      api,
      access: _token('old', const Duration(minutes: 1)),
    );

    await container.read(authProvider.notifier).signOut();
    await settle();
    final callsAtSignOut = api.refreshCalls;

    // Even asked directly, a signed-out notifier must not renew.
    await container.read(authProvider.notifier).ensureFreshToken();
    await settle();

    expect(api.refreshCalls, callsAtSignOut);
    expect(ConfigStore.authSession(), isNull,
        reason: 'a renewed session must not reappear in the keystore');
  });
}
