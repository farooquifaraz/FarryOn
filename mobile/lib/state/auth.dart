import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:google_sign_in/google_sign_in.dart';

import '../core/config.dart';
import '../core/config_store.dart';
import '../core/data_cache.dart';
import '../core/jwt.dart';
import '../core/logger.dart';
import '../core/outbox.dart';
import '../data/auth_api.dart';
import 'providers.dart';

/// The **Web** OAuth client id from the Google Cloud console, supplied at build
/// time: `flutter run --dart-define=GOOGLE_SERVER_CLIENT_ID=...`.
///
/// Not the Android client id — `google_sign_in` passes this as `serverClientId`
/// so Google mints the ID token with it as the audience, which is what the
/// backend verifies against its own `GOOGLE_CLIENT_ID`. The two MUST match.
/// Left empty, the Google button hides itself rather than failing on tap.
const String? googleServerClientId =
    bool.hasEnvironment('GOOGLE_SERVER_CLIENT_ID')
        ? String.fromEnvironment('GOOGLE_SERVER_CLIENT_ID')
        : null;

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
    ref.onDispose(() {
      _heartbeat?.cancel();
      _heartbeat = null;
    });
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
    _startHeartbeat();
  }

  // ---- Keeping the access token alive ------------------------------------
  //
  // Access tokens live 15 minutes. Until this existed, `refresh` was called
  // from exactly one place — cold start — so a session simply stopped working
  // a quarter of an hour in: the WS handshake started coming back 403, the
  // client reconnected on its 8-second backoff forever with the same dead
  // token, and the screen went on saying "Listening…" the whole time. Seen on
  // the S23 on 2026-08-11 while testing translation; it affects the assistant
  // just the same, and only a full restart cleared it.

  // The first attempt at this armed ONE timer for the moment a renewal was
  // due. It failed on the S23 the same day: a token minted at 14:13 was still
  // being sent at 15:06, nearly an hour after it expired, with no renewal ever
  // attempted. One missed timer — a request that never returned, a frozen
  // process, a rebuild — and renewal was off for the rest of the run, silently.
  //
  // So nothing now depends on a single timer landing. A cheap heartbeat asks
  // "is the token nearly out?" every [_checkEvery]; if any one tick is lost the
  // next one recovers. The check is a subtraction on a cached value — it costs
  // nothing to run often and it cannot get stuck.

  Timer? _heartbeat;

  /// Set while a renewal is in flight, so ticks do not stack up requests.
  bool _renewing = false;

  /// How often to look at the clock. Frequent enough that a missed tick is
  /// harmless, cheap enough to be free.
  static const _checkEvery = Duration(seconds: 30);

  /// Renew once the token has this little life left. Wide enough to absorb a
  /// slow network and a device that woke up late.
  static const _renewMargin = Duration(minutes: 3);

  /// A renewal that hangs must not disable renewal. Without this the request
  /// could sit forever, `_renewing` would stay true, and every later tick would
  /// skip — which is exactly the failure this rewrite exists to end.
  static const _renewTimeout = Duration(seconds: 20);

  void _startHeartbeat() {
    _heartbeat?.cancel();
    _heartbeat = Timer.periodic(_checkEvery, (_) => unawaited(_tick()));
  }

  Future<void> _tick() async {
    if (_renewing || !state.isSignedIn) return;
    final session = ConfigStore.authSession();
    if (session == null) return;
    if (jwtExpiresWithin(session.access, _renewMargin)) await _renew();
  }

  /// Trade the stored refresh token for a fresh pair. Silent by design: a
  /// failure here must not sign anyone out — the refresh token is good for 30
  /// days, so a renewal that fails because the network is down is simply tried
  /// again on the next tick.
  Future<void> _renew() async {
    final session = ConfigStore.authSession();
    if (session == null || _renewing) return;
    _renewing = true;
    final epoch = _sessionEpoch;
    try {
      final outcome = await ref
          .read(authApiProvider)
          .refresh(session.refresh)
          .timeout(_renewTimeout,
              onTimeout: () => const AuthRefreshOutcome.unreachable());
      if (epoch != _sessionEpoch) return; // signed out while we waited

      if (outcome.invalid) {
        // The server disowned the session (revoked, or signed out elsewhere).
        // Staying "signed in" on tokens the server rejects is the state that
        // produced the endless silent reconnect, so end it honestly.
        _log.warn('refresh token rejected — signing out');
        await _clearLocal();
        return;
      }
      final tokens = outcome.tokens;
      if (tokens == null) return; // unreachable; the next tick tries again
      await _persist(
        () => ConfigStore.saveAuthSession(
          access: tokens.accessToken,
          refresh: tokens.refreshToken,
        ),
        'Renewing the session',
      );
      if (epoch != _sessionEpoch) return;
      _applyTokenToConfig(tokens.accessToken);
    } finally {
      _renewing = false;
    }
  }

  /// Renew now if the token is spent, and make sure the heartbeat is running.
  ///
  /// Call this when the app comes back to the foreground: timers do not fire
  /// while Android has the process frozen, so a phone left in a pocket comes
  /// back with a dead token and a heartbeat that has not ticked in an hour.
  Future<void> ensureFreshToken() async {
    if (!state.isSignedIn) return;
    _startHeartbeat();
    await _tick();
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

  /// "Continue with Google": open the native account sheet, then trade the
  /// resulting ID token for a FarryOn session. One button covers sign-in AND
  /// sign-up — the backend creates the account if the (Google-verified) email
  /// is new, so there is nothing for the user to choose.
  ///
  /// [serverClientId] must be the **Web** OAuth client id: Google mints the
  /// ID token with it as the audience so the backend can verify it.
  Future<AuthResult> signInWithGoogle() async {
    const clientId = googleServerClientId;
    if (clientId == null || clientId.isEmpty) {
      return const AuthResult.failure(
          'Google sign-in isn\'t set up in this build.');
    }

    final GoogleSignInAccount? account;
    try {
      // A fresh instance per attempt: a stale one holds the previous account,
      // so a user who cancels and retries would silently re-sign-in as them.
      final google = GoogleSignIn(serverClientId: clientId, scopes: const ['email']);
      await google.signOut(); // always show the picker, never a silent re-auth
      account = await google.signIn();
    } catch (e) {
      _log.error('Google sign-in sheet failed', e);
      return const AuthResult.failure(
          "Couldn't open Google sign-in. Try again.");
    }
    if (account == null) {
      // The user dismissed the sheet — not an error, just nothing to report.
      return const AuthResult.cancelled();
    }

    final String? idToken;
    try {
      idToken = (await account.authentication).idToken;
    } catch (e) {
      _log.error('Google auth token fetch failed', e);
      return const AuthResult.failure("Couldn't get your Google details.");
    }
    if (idToken == null || idToken.isEmpty) {
      // Almost always a misconfigured serverClientId / SHA-1 (see the setup
      // notes in docs) — Google returns an account but no ID token.
      return const AuthResult.failure(
          'Google sign-in isn\'t configured correctly for this app.');
    }

    final result = await ref.read(authApiProvider).googleSignIn(idToken);
    if (result.tokens != null) {
      await _completeSignIn(result.tokens!, email: account.email);
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
    // Signing out is a LOCAL act, and it has to FEEL like one: the person
    // tapped "Sign out" and the screen must change now. This used to await the
    // backend logout (a network round-trip) and then a disconnect with a 2 s
    // timeout guard BEFORE touching any local state — the button sat there for
    // seconds, longer if the backend was slow. Now the tokens drop and the UI
    // flips first; the server-side revocation is fired after, best-effort
    // (logout() already swallows its own failures). The refresh token is
    // captured before _clearLocal wipes it.
    final session = ConfigStore.authSession();
    await _clearLocal();
    if (session != null) {
      unawaited(ref.read(authApiProvider).logout(session.refresh));
    }
  }

  Future<void> _clearLocal() async {
    // Invalidate any in-flight rotation first: without this its late response
    // would write the rotated tokens back, leaving a signed-out user with a
    // live session in the keystore that signs them in again next launch.
    _sessionEpoch++;
    // The heartbeat is the same hazard on a longer fuse: left running, it
    // would refresh a session nobody is signed into and write it back.
    _heartbeat?.cancel();
    _heartbeat = null;

    // Stop the live session — it outlives the home screen (LiveController is a
    // plain Provider), so sign-out would otherwise leave the mic and a
    // tokenless reconnect loop running behind the login screen. NOT awaited:
    // the teardown (gateway close, mic release) takes real time and can hang in
    // connect-retry, and none of it needs to hold up the screen change — the
    // session it's tearing down belongs to nobody now. The timeout guard keeps
    // the background cleanup itself from running forever.
    unawaited(ref
        .read(liveProvider.notifier)
        .disconnect()
        .timeout(const Duration(seconds: 2), onTimeout: () {}));

    await _persist(ConfigStore.clearAuthSession, 'Clearing the stored session');
    // Drop the cached notes/tasks with the session. They're the leaving user's,
    // the phone's storage isn't encrypted, and the server still has them — the
    // next sign-in pulls them back.
    await _persist(DataCache.clear, 'Clearing the cached notes and tasks');
    await _persist(Outbox.clear, 'Clearing the queued changes');
    final cfg = ref.read(configProvider);
    if (cfg.authToken != null) {
      ref.read(configProvider.notifier).state = cfg.copyWith(clearToken: true);
    }
    state = const AuthState.signedOut();
  }
}

final authProvider = NotifierProvider<AuthNotifier, AuthState>(AuthNotifier.new);
