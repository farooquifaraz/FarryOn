import 'package:farryon/core/config_store.dart';
import 'package:farryon/data/auth_api.dart';
import 'package:farryon/state/auth.dart';
import 'package:farryon/state/live_state.dart';
import 'package:farryon/state/providers.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Signing out must FEEL local: tap → screen changes, now.
///
/// Device-reported 2026-07-25 ("bht slow signout"): signOut() awaited the
/// backend logout round-trip and then a live-session disconnect with a 2 s
/// timeout guard BEFORE touching any local state, so the button sat there for
/// seconds — longer when the backend was slow. The fix flips local state first
/// and fires the server-side revocation after, best-effort. These tests pin
/// that by making both slow (5 s each) and requiring signOut() to return
/// almost immediately anyway — while still proving the revocation call does
/// eventually go out with the right token.
class _SlowApi implements AuthApi {
  String? loggedOutWith;

  @override
  Future<void> logout(String refreshToken) async {
    await Future<void>.delayed(const Duration(seconds: 5));
    loggedOutWith = refreshToken;
  }

  // The notifier's build() kicks off a restore; "unreachable" keeps the stored
  // session as-is, which is exactly the signed-in starting point we want.
  @override
  Future<AuthRefreshOutcome> refresh(String refreshToken) async =>
      const AuthRefreshOutcome.unreachable();

  @override
  Future<AuthUser?> me(String accessToken) async => null;

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _SlowLive extends LiveNotifier {
  bool disconnectStarted = false;

  @override
  LiveSessionState build() => const LiveSessionState();

  @override
  Future<void> disconnect() async {
    disconnectStarted = true;
    await Future<void>.delayed(const Duration(seconds: 5));
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  final keystore = <String, String>{};
  setUp(() async {
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
    SharedPreferences.setMockInitialValues({});
    await ConfigStore.init();
    await ConfigStore.saveAuthSession(
      access: 'a-token',
      refresh: 'r-token',
      email: 'f@example.com',
    );
  });

  tearDown(() async => ConfigStore.clearAuthSession());

  test('signOut returns fast even when the network and teardown are slow',
      () async {
    final api = _SlowApi();
    final live = _SlowLive();
    final container = ProviderContainer(overrides: [
      authApiProvider.overrideWithValue(api),
      liveProvider.overrideWith(() => live),
    ]);
    addTearDown(container.dispose);

    final sw = Stopwatch()..start();
    await container.read(authProvider.notifier).signOut();
    sw.stop();

    // Local writes only — nothing near the 5 s the network/teardown take. The
    // generous bound keeps CI happy; the old code took >5 s here by design.
    expect(sw.elapsedMilliseconds, lessThan(1000),
        reason: 'sign-out must not wait for the network or the mic teardown');
    expect(container.read(authProvider).isSignedIn, isFalse);
    expect(ConfigStore.authSession(), isNull,
        reason: 'the stored session is gone immediately');
    expect(live.disconnectStarted, isTrue,
        reason: 'the mic teardown still starts — it just does not block');
  });

  test('the server-side revocation still happens, with the right token',
      () async {
    final api = _SlowApi();
    final container = ProviderContainer(overrides: [
      authApiProvider.overrideWithValue(api),
      liveProvider.overrideWith(_SlowLive.new),
    ]);
    addTearDown(container.dispose);

    await container.read(authProvider.notifier).signOut();
    expect(api.loggedOutWith, isNull, reason: 'not yet — it is in flight');

    // Let the fire-and-forget call finish.
    await Future<void>.delayed(const Duration(seconds: 6));
    expect(api.loggedOutWith, 'r-token',
        reason: 'revocation must go out with the refresh token captured '
            'BEFORE local state was wiped');
  });
}
