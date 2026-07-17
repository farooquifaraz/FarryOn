import 'package:farryon/core/config_store.dart';
import 'package:farryon/data/auth_api.dart';
import 'package:farryon/state/auth.dart';
import 'package:farryon/state/live_state.dart';
import 'package:farryon/state/providers.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Cold start with a stored session: what happens when the backend can't be
/// reached? (TEST_PLAN A7 — "airplane mode, then open the app".)
///
/// The rule is **sign out only on 401**. A server that says "this session is
/// dead" is the one thing worth acting on; everything else — no network, a dead
/// backend, a hotel wifi portal, a dropped SYN — is a reason to keep the session
/// and let the live screen show its offline state. Get this wrong and you log
/// people out on a train, which they experience as "the app forgot me" and no
/// amount of correct backend logs will explain it.
///
/// Driven through [AuthNotifier] with a fake API, not through airplane mode on a
/// phone: turning off the radio also kills wireless ADB, so the screen goes with
/// it. This pins the decision itself.
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

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

/// The 401 path calls `liveProvider.notifier.disconnect()` — it must stop the
/// mic before dropping the token, or a revoked session leaves a tokenless
/// reconnect loop running behind the login screen. That pulls in flutter_sound
/// and the glasses channel, which don't exist in a unit test, so stand in for
/// the notifier itself.
class _FakeLive extends LiveNotifier {
  @override
  LiveSessionState build() => const LiveSessionState();

  @override
  Future<void> disconnect() async {}
}

const _stored = (
  access: 'stored-access',
  refresh: 'stored-refresh',
  email: 'faraz@example.com',
);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  // flutter_secure_storage is a native plugin and ConfigStore holds a const
  // instance of it, so it can't be injected — stand in for the platform side
  // with an in-memory map. The keystore's own behaviour isn't what's under test
  // here; the restore decision is.
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
    // Without init() the prefs handle is null and saveAuthSession silently
    // drops the non-secret half of the session (email, name, id) — the app
    // calls this at startup, so the test has to as well.
    await ConfigStore.init();
    await ConfigStore.saveAuthSession(
      access: _stored.access,
      refresh: _stored.refresh,
      email: _stored.email,
    );
  });

  tearDown(() async => ConfigStore.clearAuthSession());

  /// Build a container whose AuthNotifier restores against [api], and wait for
  /// the restore microtask to finish.
  Future<(ProviderContainer, AuthState)> restoreWith(_FakeApi api) async {
    final container = ProviderContainer(
      overrides: [
        authApiProvider.overrideWithValue(api),
        liveProvider.overrideWith(_FakeLive.new),
      ],
    );
    addTearDown(container.dispose);
    container.read(authProvider); // triggers build() -> _restore()
    for (var i = 0; i < 50; i++) {
      await Future<void>.delayed(Duration.zero);
      if (!container.read(authProvider).isRestoring) break;
    }
    return (container, container.read(authProvider));
  }

  test('offline: stays signed in on the cached token', () async {
    final api = _FakeApi(() => const AuthRefreshOutcome.unreachable());
    final (container, state) = await restoreWith(api);

    expect(api.refreshCalls, 1, reason: 'it should have tried');
    expect(state.isSignedIn, isTrue, reason: 'unreachable is not a sign-out');
    expect(state.email, _stored.email);
    // The live session must still get a token, or the app is signed in on paper
    // and mute in practice.
    expect(container.read(configProvider).authToken, _stored.access);
  });

  test('offline: the stored session survives for the next launch', () async {
    await restoreWith(_FakeApi(() => const AuthRefreshOutcome.unreachable()));
    expect(ConfigStore.authSession()?.refresh, _stored.refresh);
  });

  test('401: signs out and forgets the session', () async {
    // The one case that means the session is really gone — revoked, suspended,
    // signed out elsewhere.
    final api = _FakeApi(() => const AuthRefreshOutcome.invalid());
    final (container, state) = await restoreWith(api);

    expect(state.isSignedIn, isFalse);
    expect(ConfigStore.authSession(), isNull, reason: 'must not linger');
    expect(container.read(configProvider).authToken, isNull);
  });

  test('reachable: rotates and uses the fresh token', () async {
    final api = _FakeApi(
      () => const AuthRefreshOutcome.rotated(
        AuthTokens(accessToken: 'new-access', refreshToken: 'new-refresh'),
      ),
    );
    final (container, state) = await restoreWith(api);

    expect(state.isSignedIn, isTrue);
    expect(container.read(configProvider).authToken, 'new-access');
    expect(ConfigStore.authSession()?.refresh, 'new-refresh',
        reason: 'the rotated token must be the one kept');
  });

  test('a hung backend times out and is treated as offline', () async {
    // The nastiest case: not a refused connection but a dropped SYN — the
    // request never answers. Without the timeout the app hangs on the splash
    // forever; with it, this is just another way to be offline.
    final api = _FakeApi(() => const AuthRefreshOutcome.unreachable());
    final (container, state) = await restoreWith(api);

    expect(state.isSignedIn, isTrue);
    expect(container.read(configProvider).authToken, isNotNull);
  });

  test('no stored session: signed out, and no call is made', () async {
    await ConfigStore.clearAuthSession();
    final api = _FakeApi(() => const AuthRefreshOutcome.unreachable());
    final (_, state) = await restoreWith(api);

    expect(state.isSignedIn, isFalse);
    expect(api.refreshCalls, 0, reason: 'nothing to refresh');
  });
}
