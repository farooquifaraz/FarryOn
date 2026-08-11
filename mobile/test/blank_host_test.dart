import 'package:farryon/core/protocol_url.dart';
import 'package:flutter_test/flutter_test.dart';

/// A blank server host must never reach the socket layer.
///
/// Device-seen 2026-08-11: retyping a server address left `cfg.host` saved as
/// an empty string. `buildLiveUri` then produced `ws://:8000/ws/live` — a URI
/// that can never resolve — and the client spun in its reconnect loop hard
/// enough to trip Android's "Farry isn't responding" dialog on EVERY launch.
/// The settings screen was behind that dialog, so there was no way back in to
/// correct the value: the app had bricked itself with one empty text field.
void main() {
  test('an empty host falls back rather than producing an unusable URI', () {
    final uri = buildLiveUri(host: '', port: 8000, secure: false);
    expect(uri.host, isNotEmpty);
    expect(uri.toString(), isNot(contains('://:')));
  });

  test('a whitespace-only host is treated the same way', () {
    final uri = buildLiveUri(host: '   ', port: 8000, secure: false);
    expect(uri.host, isNotEmpty);
  });

  test('a real host is still used verbatim', () {
    expect(
      buildLiveUri(host: '192.168.1.107', port: 8000, secure: false).host,
      '192.168.1.107',
    );
  });

  test('a pasted URL is still reduced to the bare host', () {
    expect(
      buildLiveUri(host: 'https://example.com/ws/live', port: 443, secure: true)
          .host,
      'example.com',
    );
  });
}
