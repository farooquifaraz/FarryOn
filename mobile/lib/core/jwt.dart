/// Reading the `exp` claim out of an access token, without verifying it.
///
/// The signature is the server's business — the client cannot check it and has
/// no reason to. All this answers is "how long is this token good for", so the
/// app can renew *before* the socket is refused rather than after.
///
/// Everything here returns null rather than throwing: a token we cannot parse
/// is treated as one whose expiry is unknown, and callers fall back to their
/// existing behaviour.
library;

import 'dart:convert';

/// The `exp` claim as UTC time, or null if absent/unparseable.
DateTime? jwtExpiry(String? token) {
  final claims = jwtClaims(token);
  final exp = claims?['exp'];
  if (exp is! int) return null;
  return DateTime.fromMillisecondsSinceEpoch(exp * 1000, isUtc: true);
}

/// Whether [token] is expired, or will be within [margin].
///
/// An unreadable or expiry-less token counts as **not** expired: refusing to
/// use a token we merely failed to parse would sign people out over a format
/// we did not anticipate.
bool jwtExpiresWithin(String? token, Duration margin) {
  final exp = jwtExpiry(token);
  if (exp == null) return false;
  return DateTime.now().toUtc().add(margin).isAfter(exp);
}

/// The decoded payload, or null if the token is not a readable JWT.
Map<String, Object?>? jwtClaims(String? token) {
  if (token == null || token.isEmpty) return null;
  final parts = token.split('.');
  if (parts.length != 3) return null;
  try {
    // base64url without padding is what JWT uses; Dart's decoder wants it.
    final payload = parts[1];
    final padded = payload.padRight((payload.length + 3) ~/ 4 * 4, '=');
    final decoded = utf8.decode(base64Url.decode(padded));
    final map = jsonDecode(decoded);
    return map is Map ? map.cast<String, Object?>() : null;
  } catch (_) {
    return null;
  }
}
