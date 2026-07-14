import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/config.dart';
import '../core/config_store.dart';
import '../core/logger.dart';
import '../data/auth_api.dart';
import 'providers.dart';

/// Whether the user is signed in to their FarryOn account. `signedIn` carries
/// the cached identity for greetings/settings; tokens live in [ConfigStore]
/// (keystore), never in state.
///
/// [AuthStatus.restoring] is the cold-start step between "a session exists in
/// the keystore" and "we know which token to hand the live session". It exists
/// so the home screen mounts EXACTLY ONCE, with the final token: [LiveScreen]
/// auto-connects the moment it mounts, and swapping the token afterwards would
/// force an immediate reconnect (AppConfig has no `==`, so any change makes
/// LiveNotifier re-point the client — see state/providers.dart).
enum AuthStatus { restoring, signedOut, signedIn }

class AuthState {
  const AuthState.restoring()
      : status = AuthStatus.restoring,
        email = '',
        displayName = null,
        userId = null;
  const AuthState.signedOut()
      : status = AuthStatus.signedOut,
        email = '',
        displayName = null,
        userId = null;
  const AuthState.signedIn({
    required this.email,
    this.displayName,
    this.userId,
  }) : status = AuthStatus.signedIn;

  final AuthStatus status;
  final String email;
  final String? displayName;
  final int? userId;

  bool get isSignedIn => status == AuthStatus.signedIn;
  bool get isRestoring => status == AuthStatus.restoring;
}

/// REST client for `/api/v1/auth/*`; tracks the current backend config.
final authApiProvider = Provider<AuthApi>((ref) {
  final api = AuthApi(ref.read(configProvider));
  ref.listen<AppConfig>(configProvider, (_, next) => api.updateConfig(next));
  ref.onDispose(api.dispose);
  return api;
});

class AuthNotifier extends Notifier<AuthState> {
  static final _log = Logger('Auth');

  /// The keystore can throw (`PlatformException` on Android key invalidation
  /// after a backup-restore, etc.). Persistence failing must never break the
  /// sign-in itself — [AuthApi] is a never-throw boundary and the screens
  /// rely on that, so an exception escaping here would leave the submit
  /// button stuck on "Signing in…" forever. Degrade instead: the session
  /// works now, it just won't survive a restart.
  Future<void> _persist(Future<void> Function() write, String what) async {
    try {
      await write();
    } catch (e, s) {
      _log.error('$what failed — session is memory-only this run', e, s);
    }
  }

  /// Set when a sign-out happens while [_restore]'s rotation is still in
  /// flight, so its late response can't write a ghost session back into the
  /// keystore that would silently sign the user in again next launch.
  int _sessionEpoch = 0;

  /// How long cold start waits for the token rotation before falling back to
  /// the cached token. Short: this is the splash the user stares at, and an
  /// unreachable backend must not hold the app hostage.
  static const _restoreTimeout = Duration(seconds: 4);

  @override
  AuthState build() {
    final session = ConfigStore.authSession();
    if (session == null) return const AuthState.signedOut();

    // Riverpod forbids writing another provider (configProvider) during this
    // provider's initialization, so the work is deferred a microtask.
    Future.microtask(() => _restore(session));
    return const AuthState.restoring();
  }

  /// Cold start: rotate the stored tokens, THEN decide. Nothing touches
  /// configProvider (and so the live session) until the final token is known,
  /// so the home screen connects once with a token that will still be valid.
  Future<void> _restore(
    ({
      String access,
      String refresh,
      String email,
      String? displayName,
      int? userId
    }) session,
  ) async {
    final epoch = _sessionEpoch;
    final outcome = await ref
        .read(authApiProvider)
        .refresh(session.refresh)
        .timeout(_restoreTimeout,
            onTimeout: () => const AuthRefreshOutcome.unreachable());
    if (epoch != _sessionEpoch) return; // signed out while we waited

    if (outcome.invalid) {
      // Server says the session is dead (revoked / signed out elsewhere).
      await _clearLocal();
      return;
    }

    final tokens = outcome.tokens;
    if (tokens != null) {
      await _persist(
        () => ConfigStore.saveAuthSession(
          access: tokens.accessToken,
          refresh: tokens.refreshToken,
        ),
        'Rotating the stored session',
      );
      if (epoch != _sessionEpoch) return;
    }
    // Unreachable: fall back to the cached token (offline tolerance) — it may
    // be expired, in which case the WS handshake fails and the live screen
    // shows its normal offline state, which is the honest outcome.
    _applyTokenToConfig(tokens?.accessToken ?? session.access);
    state = AuthState.signedIn(
      email: session.email,
      displayName: session.displayName,
      userId: session.userId,
    );
  }

  void _applyTokenToConfig(String accessToken) {
    final cfg = ref.read(configProvider);
    if (cfg.authToken != accessToken) {
      ref.read(configProvider.notifier).state =
          cfg.copyWith(authToken: accessToken);
    }
  }

  /// Completes sign-in after any flow (password, 2FA) that produced tokens:
  /// fetch the profile, persist everything, point the WS config at the token.
  Future<void> _completeSignIn(AuthTokens tokens, {required String email}) async {
    final user = await ref.read(authApiProvider).me(tokens.accessToken);
    await _persist(
      () => ConfigStore.saveAuthSession(
        access: tokens.accessToken,
        refresh: tokens.refreshToken,
        email: user?.email ?? email,
        displayName: user?.displayName,
        userId: user?.id,
      ),
      'Saving the session',
    );
    _applyTokenToConfig(tokens.accessToken);
    state = AuthState.signedIn(
      email: user?.email ?? email,
      displayName: user?.displayName,
      userId: user?.id,
    );
  }

  /// Sign in with email + password. Returns the raw [AuthResult] so the
  /// login screen can branch on failure message or a 2FA challenge.
  Future<AuthResult> signIn(String email, String password) async {
    final result =
        await ref.read(authApiProvider).login(email: email, password: password);
    if (result.tokens != null) {
      await _completeSignIn(result.tokens!, email: email);
    }
    return result;
  }

  /// Complete a 2FA challenge started by [signIn].
  Future<AuthResult> verify2fa({
    required String pendingToken,
    required String code,
    required String email,
  }) async {
    final result = await ref
        .read(authApiProvider)
        .verify2fa(pendingToken: pendingToken, code: code);
    if (result.tokens != null) {
      await _completeSignIn(result.tokens!, email: email);
    }
    return result;
  }

  /// Create an account, then sign straight in.
  Future<AuthResult> signUp({
    required String email,
    required String password,
    String? displayName,
  }) async {
    final result = await ref.read(authApiProvider).register(
        email: email, password: password, displayName: displayName);
    if (result.tokens != null) {
      await _completeSignIn(result.tokens!, email: email);
    }
    return result;
  }

  /// Sign out: best-effort server revoke, then always clear locally.
  Future<void> signOut() async {
    final session = ConfigStore.authSession();
    if (session != null) {
      await ref.read(authApiProvider).logout(session.refresh);
    }
    await _clearLocal();
  }

  Future<void> _clearLocal() async {
    // Invalidate any in-flight rotation first: without this its late response
    // would write the rotated tokens back, leaving a signed-out user with a
    // live session in the keystore that signs them in again next launch.
    _sessionEpoch++;

    // Stop the live session before dropping the token. It outlives the home
    // screen (LiveController is a plain Provider), so a revoked-session
    // sign-out would otherwise leave the mic and a tokenless reconnect loop
    // running behind the login screen. Timeout-guarded for the same reason as
    // the Settings sign-out: a session stuck in connect-retry never resolves
    // disconnect().
    await ref
        .read(liveProvider.notifier)
        .disconnect()
        .timeout(const Duration(seconds: 2), onTimeout: () {});

    await _persist(ConfigStore.clearAuthSession, 'Clearing the stored session');
    final cfg = ref.read(configProvider);
    if (cfg.authToken != null) {
      ref.read(configProvider.notifier).state = cfg.copyWith(clearToken: true);
    }
    state = const AuthState.signedOut();
  }
}

final authProvider = NotifierProvider<AuthNotifier, AuthState>(AuthNotifier.new);
