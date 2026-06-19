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
  return Uri(
    scheme: secure ? 'wss' : 'ws',
    host: host,
    port: port,
    path: '/ws/live',
    queryParameters:
        (token != null && token.isNotEmpty) ? {'token': token} : null,
  );
}
