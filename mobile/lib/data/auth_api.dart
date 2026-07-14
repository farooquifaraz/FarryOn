import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import '../core/config.dart';

/// The signed-in user's public profile (subset of `GET /api/v1/me`).
class AuthUser {
  const AuthUser({
    required this.id,
    required this.email,
    this.displayName,
  });

  final int id;
  final String email;
  final String? displayName;

  factory AuthUser.fromJson(Map<String, dynamic> j) => AuthUser(
        id: (j['id'] as num).toInt(),
        email: j['email'] as String? ?? '',
        displayName: j['display_name'] as String?,
      );
}

/// Access + refresh token pair returned by login/refresh.
class AuthTokens {
  const AuthTokens({required this.accessToken, required this.refreshToken});
  final String accessToken;
  final String refreshToken;
}

/// Outcome of a sign-in/sign-up call. Exactly one of the "success" shapes is
/// set: [tokens] (fully signed in), [pendingToken] (server wants a 2FA code —
/// call [AuthApi.verify2fa] next), or [accountCreated] (sign-up worked but the
/// follow-up sign-in didn't — the account EXISTS, so the user must be sent to
/// the sign-in screen, never told the sign-up failed). On failure only
/// [message] is set.
///
/// Like `email_probe.dart`, this NEVER throws to the UI — every failure mode
/// (timeout, no route, bad credentials, server error) folds into
/// `ok == false` + a human-readable [message] the screen can show inline.
class AuthResult {
  const AuthResult._({
    required this.ok,
    required this.message,
    this.tokens,
    this.pendingToken,
    this.user,
    this.accountCreated = false,
  });

  const AuthResult.success({AuthTokens? tokens, AuthUser? user})
      : this._(ok: true, message: '', tokens: tokens, user: user);
  const AuthResult.twoFactor(String pendingToken)
      : this._(ok: true, message: '', pendingToken: pendingToken);
  const AuthResult.failure(String message)
      : this._(ok: false, message: message);

  /// The account was created, but signing in right after it failed (e.g. the
  /// email was rate-limited by earlier failed logins, or the network dropped
  /// between the two calls). [message] explains why sign-in didn't happen.
  const AuthResult.accountCreatedNotSignedIn(String message)
      : this._(ok: true, message: message, accountCreated: true);

  final bool ok;
  final String message;
  final AuthTokens? tokens;
  final String? pendingToken;
  final AuthUser? user;

  /// True only for [AuthResult.accountCreatedNotSignedIn].
  final bool accountCreated;

  bool get needsTwoFactor => pendingToken != null;
}

/// Thin REST client for the backend's `/api/v1/auth/*` + `/api/v1/me`
/// endpoints. Same shape as [DataApi]: injectable client, mutable config.
class AuthApi {
  AuthApi(this._config, {http.Client? client})
      : _client = client ?? http.Client();

  AppConfig _config;
  final http.Client _client;

  void updateConfig(AppConfig config) => _config = config;

  static const _timeout = Duration(seconds: 20);
  static const _jsonHeaders = {'Content-Type': 'application/json'};

  Uri _uri(String path) => _config.httpBase.replace(path: path);

  /// POST helper. Throws on transport failure or an unparseable body; callers
  /// fold that into a human message via [_transportFailure].
  Future<({int status, Map<String, dynamic> body})> _post(
    String path,
    Map<String, Object?> body,
  ) async {
    final r = await _client
        .post(_uri(path), headers: _jsonHeaders, body: jsonEncode(body))
        .timeout(_timeout);
    return (status: r.statusCode, body: jsonDecode(r.body) as Map<String, dynamic>);
  }

  static String _envelopeError(Map<String, dynamic> envelope, String fallback) {
    final error = envelope['error'];
    if (error is Map && error['message'] is String) {
      return error['message'] as String;
    }
    return fallback;
  }

  static AuthResult _transportFailure(Object e) {
    if (e is TimeoutException) {
      return const AuthResult.failure(
          'Timed out reaching the server — check the server address in settings.');
    }
    if (e is SocketException) {
      return const AuthResult.failure(
          "Can't reach the server — check your connection and the server address.");
    }
    return const AuthResult.failure('Something went wrong. Try again.');
  }

  static AuthTokens? _tokensFrom(Map<String, dynamic> data) {
    final access = data['access_token'] as String?;
    final refresh = data['refresh_token'] as String?;
    if (access == null || refresh == null) return null;
    return AuthTokens(accessToken: access, refreshToken: refresh);
  }

  /// `POST /api/v1/auth/register` then, on success, signs straight in.
  Future<AuthResult> register({
    required String email,
    required String password,
    String? displayName,
  }) async {
    try {
      final res = await _post('/api/v1/auth/register', {
        'email': email,
        'password': password,
        if (displayName != null && displayName.trim().isNotEmpty)
          'display_name': displayName.trim(),
      });
      if (res.body['success'] != true) {
        return AuthResult.failure(_envelopeError(
            res.body, "Couldn't create the account. Try again."));
      }
      // Registration returns the profile, not tokens — log in for the tokens.
      // Past this point the account EXISTS: a failing sign-in must never be
      // reported as a failed sign-up, or the user retries and hits
      // "email already exists" on an account that is really theirs.
      final signIn = await login(email: email, password: password);
      if (signIn.tokens != null || signIn.needsTwoFactor) return signIn;
      return AuthResult.accountCreatedNotSignedIn(signIn.message);
    } catch (e) {
      return _transportFailure(e);
    }
  }

  /// `POST /api/v1/auth/login`. May resolve to a 2FA challenge.
  Future<AuthResult> login({
    required String email,
    required String password,
  }) async {
    try {
      final res = await _post('/api/v1/auth/login', {
        'email': email,
        'password': password,
      });
      if (res.body['success'] != true) {
        return AuthResult.failure(
            _envelopeError(res.body, 'Incorrect email or password.'));
      }
      final data = (res.body['data'] as Map).cast<String, dynamic>();
      if (data['two_factor_required'] == true) {
        return AuthResult.twoFactor(data['pending_token'] as String);
      }
      final tokens = _tokensFrom(data);
      if (tokens == null) {
        return const AuthResult.failure('Unexpected server response.');
      }
      return AuthResult.success(tokens: tokens);
    } catch (e) {
      return _transportFailure(e);
    }
  }

  /// `POST /api/v1/auth/2fa/verify-login` — exchange the pending token +
  /// authenticator/recovery code for real tokens.
  Future<AuthResult> verify2fa({
    required String pendingToken,
    required String code,
  }) async {
    try {
      final res = await _post('/api/v1/auth/2fa/verify-login', {
        'pending_token': pendingToken,
        'code': code,
      });
      if (res.body['success'] != true) {
        return AuthResult.failure(
            _envelopeError(res.body, "That code didn't match. Try again."));
      }
      final tokens =
          _tokensFrom((res.body['data'] as Map).cast<String, dynamic>());
      if (tokens == null) {
        return const AuthResult.failure('Unexpected server response.');
      }
      return AuthResult.success(tokens: tokens);
    } catch (e) {
      return _transportFailure(e);
    }
  }

  /// `POST /api/v1/auth/refresh` — rotate the session.
  ///
  /// [AuthRefreshOutcome.invalid] (the only outcome that signs the user out)
  /// is reported ONLY when the server explicitly rejects the token with 401.
  /// Anything else — a 500, a bad gateway, a malformed body, no network — is
  /// [AuthRefreshOutcome.unreachable], because treating a transient backend
  /// hiccup as "your session is dead" would sign every user out at once.
  Future<AuthRefreshOutcome> refresh(String refreshToken) async {
    try {
      final res =
          await _post('/api/v1/auth/refresh', {'refresh_token': refreshToken});
      if (res.status == 401) return const AuthRefreshOutcome.invalid();
      if (res.body['success'] != true) {
        return const AuthRefreshOutcome.unreachable();
      }
      final tokens =
          _tokensFrom((res.body['data'] as Map).cast<String, dynamic>());
      if (tokens == null) return const AuthRefreshOutcome.unreachable();
      return AuthRefreshOutcome.rotated(tokens);
    } catch (_) {
      return const AuthRefreshOutcome.unreachable();
    }
  }

  /// `POST /api/v1/auth/logout` — best-effort server-side revoke.
  Future<void> logout(String refreshToken) async {
    try {
      await _post('/api/v1/auth/logout', {'refresh_token': refreshToken});
    } catch (_) {
      // Local sign-out proceeds regardless.
    }
  }

  /// `GET /api/v1/me` with the given access token.
  Future<AuthUser?> me(String accessToken) async {
    try {
      final r = await _client.get(
        _uri('/api/v1/me'),
        headers: {'Authorization': 'Bearer $accessToken'},
      ).timeout(_timeout);
      final envelope = jsonDecode(r.body) as Map<String, dynamic>;
      if (envelope['success'] != true) return null;
      return AuthUser.fromJson(
          (envelope['data'] as Map).cast<String, dynamic>());
    } catch (_) {
      return null;
    }
  }

  void dispose() => _client.close();
}

/// Refresh has three distinct outcomes; conflating "server said no" with
/// "couldn't reach the server" would sign users out on every airplane-mode
/// launch, so they're kept separate.
class AuthRefreshOutcome {
  const AuthRefreshOutcome.rotated(AuthTokens this.tokens)
      : invalid = false,
        unreachable = false;
  const AuthRefreshOutcome.invalid()
      : tokens = null,
        invalid = true,
        unreachable = false;
  const AuthRefreshOutcome.unreachable()
      : tokens = null,
        invalid = false,
        unreachable = true;

  final AuthTokens? tokens;
  final bool invalid;
  final bool unreachable;
}
