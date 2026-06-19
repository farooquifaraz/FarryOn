import 'package:farryon/core/protocol_url.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('buildLiveUri', () {
    test('plaintext ws:// with explicit port and /ws/live path', () {
      final uri = buildLiveUri(host: 'localhost', port: 8000, secure: false);
      expect(uri.toString(), 'ws://localhost:8000/ws/live');
    });

    test('wss:// when secure', () {
      final uri = buildLiveUri(host: 'example.com', port: 443, secure: true);
      expect(uri.scheme, 'wss');
      expect(uri.host, 'example.com');
      expect(uri.path, '/ws/live');
    });

    test('appends ?token= when a token is supplied', () {
      final uri = buildLiveUri(
        host: 'h',
        port: 8000,
        secure: false,
        token: 'abc.def',
      );
      expect(uri.queryParameters['token'], 'abc.def');
    });

    test('omits the query when token is null or empty', () {
      expect(
        buildLiveUri(host: 'h', port: 1, secure: false, token: null).hasQuery,
        isFalse,
      );
      expect(
        buildLiveUri(host: 'h', port: 1, secure: false, token: '').hasQuery,
        isFalse,
      );
    });
  });
}
