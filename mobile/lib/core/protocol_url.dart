/// Pure helper for building the `/ws/live` endpoint URI.
///
/// Kept separate from [AppConfig] (and free of any Flutter imports) so it can be
/// unit-tested in plain Dart.
library;

/// Build the WebSocket URI for `/ws/live`.
///
/// * [secure] selects `wss` (true) or `ws` (false).
/// * The port is always included for explicitness.
/// * A non-empty [token] is added as `?token=<token>` per `PROTOCOL.md` §1.
Uri buildLiveUri({
  required String host,
  required int port,
  required bool secure,
  String? token,
}) {
  // Be forgiving about the host field: accept a pasted full URL (strip any
  // scheme like `https://` or `wss://`), a trailing path such as `/ws/live`,
  // and stray slashes/whitespace, reducing it to the bare hostname.
  var h = host.trim();
  h = h.replaceFirst(RegExp(r'^[a-zA-Z][a-zA-Z0-9+.-]*://'), '');
  h = h.split('/').first;
  // Last line of defence. An empty host yields `ws://:8000/ws/live`, which can
  // never connect and sends the client into a reconnect loop it cannot be
  // talked out of. Falling back to localhost fails visibly and recoverably
  // instead — and the settings screen stays reachable to fix the real value.
  if (h.isEmpty) h = 'localhost';
  return Uri(
    scheme: secure ? 'wss' : 'ws',
    host: h,
    port: port,
    path: '/ws/live',
    queryParameters:
        (token != null && token.isNotEmpty) ? {'token': token} : null,
  );
}
