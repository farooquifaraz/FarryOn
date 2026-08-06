import 'dart:async';
import 'dart:convert';

import 'package:farryon/core/config.dart';
import 'package:farryon/data/live_client.dart';
import 'package:farryon/protocol/messages.dart';
import 'package:farryon/protocol/protocol.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:stream_channel/stream_channel.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'live_client_test.dart' show FakeChannel;

/// A channel that fails the way a real refused upgrade does: the exception
/// arrives ASYNCHRONOUSLY on the stream, not from `connect` itself.
class _ErroringChannel extends StreamChannelMixin<dynamic>
    implements WebSocketChannel {
  _ErroringChannel(this._error);

  final Object _error;
  final _out = StreamController<dynamic>();

  @override
  Stream<dynamic> get stream => Stream<dynamic>.error(_error);

  @override
  WebSocketSink get sink => _FakeSink(_out);

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _FakeSink implements WebSocketSink {
  _FakeSink(this._ctl);
  final StreamController<dynamic> _ctl;

  @override
  void add(dynamic data) {}
  @override
  Future<void> close([int? closeCode, String? closeReason]) async {
    await _ctl.close();
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => null;
}

void main() {
  const cfg = AppConfig(host: 'h', port: 8000, secure: false);
  DeviceInfo device() => const DeviceInfo(
        kind: 'phone',
        id: 'test',
        capabilities: <String>[],
      );

  test('a refused handshake asks for a fresh token exactly once per drop',
      () async {
    // Access tokens expire; an outage longer than their life left the client
    // retrying the same dead credential forever ("Reconnecting" on a healthy
    // network, device-proven 2026-08-06).
    var refreshes = 0;
    final client = WebSocketLiveClient(
      config: cfg,
      platform: 'android',
      deviceInfoProvider: device,
      channelFactory: (_) => _ErroringChannel(Exception(
          'WebSocketChannelException: not upgraded to websocket (status 403)')),
    )..onAuthRejected = () async => refreshes++;

    client.start();
    // Long enough for several backoff retries, all refused the same way.
    await Future<void>.delayed(const Duration(seconds: 2));
    expect(refreshes, 1,
        reason: 'rotate once per connection generation, not once per retry — '
            'a fresh token that is ALSO refused means the problem is not '
            'staleness, and retrying would hammer the auth endpoint');
    await client.stop();
  });

  test('an ordinary network error does NOT rotate the token', () async {
    // Rotating on every blip would hammer the auth endpoint and could sign the
    // user out on a flaky train ride.
    var refreshes = 0;
    final client = WebSocketLiveClient(
      config: cfg,
      platform: 'android',
      deviceInfoProvider: device,
      channelFactory: (_) =>
          _ErroringChannel(Exception('SocketException: no route to host')),
    )..onAuthRejected = () async => refreshes++;

    client.start();
    await Future<void>.delayed(const Duration(milliseconds: 50));
    expect(refreshes, 0);
    await client.stop();
  });

  test('a healthy connect never touches the token', () async {
    final fake = FakeChannel();
    var refreshes = 0;
    final client = WebSocketLiveClient(
      config: cfg,
      platform: 'android',
      deviceInfoProvider: device,
      channelFactory: (_) => fake,
    )..onAuthRejected = () async => refreshes++;

    client.start();
    await Future<void>.delayed(const Duration(milliseconds: 50));
    expect(refreshes, 0);
    await client.stop();
  });

  test('a config-driven reconnect closes the previous socket', () async {
    // A rotated token reconnects through updateConfig while a backoff retry
    // may also be pending. Assigning over a live socket used to orphan it —
    // the backend saw four sessions for one reconnect (2026-08-06).
    final opened = <_CountingChannel>[];
    final client = WebSocketLiveClient(
      config: cfg,
      platform: 'android',
      deviceInfoProvider: device,
      channelFactory: (_) {
        final c = _CountingChannel();
        opened.add(c);
        return c;
      },
    );

    client.start();
    await Future<void>.delayed(const Duration(milliseconds: 20));
    expect(opened.length, 1);

    client.updateConfig(const AppConfig(host: 'h2', port: 8000, secure: false));
    await Future<void>.delayed(const Duration(milliseconds: 20));
    expect(opened.length, 2, reason: 'the new endpoint gets its own socket');
    expect(opened.first.closed, isTrue,
        reason: 'the previous socket must be closed, not left dangling');
    await client.stop();
  });

  test('a token change alone does not restart a healthy session', () async {
    // The REST layer rotates the token on its own 401s. Reconnecting the live
    // socket for each rotation restarted the session four times in thirteen
    // seconds after an outage (device-proven 2026-08-06).
    final opened = <_CountingChannel>[];
    final client = WebSocketLiveClient(
      config: cfg,
      platform: 'android',
      deviceInfoProvider: device,
      channelFactory: (_) {
        final c = _CountingChannel();
        opened.add(c);
        return c;
      },
    );

    client.start();
    await Future<void>.delayed(const Duration(milliseconds: 20));
    opened.first.push(jsonEncode({
      'type': 'ready',
      'sessionId': 's1',
      'protocolVersion': kProtocolVersion,
    }));
    await Future<void>.delayed(const Duration(milliseconds: 20));

    // Same endpoint, only the token moved.
    client.updateConfig(const AppConfig(
        host: 'h', port: 8000, secure: false, authToken: 'fresh'));
    await Future<void>.delayed(const Duration(milliseconds: 20));
    expect(opened.length, 1, reason: 'the live socket must survive a rotation');
    expect(opened.first.closed, isFalse);

    // A real endpoint change still reconnects.
    client.updateConfig(
        const AppConfig(host: 'other', port: 8000, secure: false));
    await Future<void>.delayed(const Duration(milliseconds: 20));
    expect(opened.length, 2);
    await client.stop();
  });
}

/// Records whether its sink was closed, so a test can prove the previous
/// socket was torn down rather than orphaned.
class _CountingChannel extends StreamChannelMixin<dynamic>
    implements WebSocketChannel {
  _CountingChannel();

  final _incoming = StreamController<dynamic>();
  bool closed = false;

  /// Feed a server message to the client under test.
  void push(dynamic data) => _incoming.add(data);

  @override
  Stream<dynamic> get stream => _incoming.stream;

  @override
  WebSocketSink get sink => _ClosingSink(this);

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _ClosingSink implements WebSocketSink {
  _ClosingSink(this._owner);
  final _CountingChannel _owner;

  @override
  void add(dynamic data) {}
  @override
  Future<void> close([int? closeCode, String? closeReason]) async {
    _owner.closed = true;
    await _owner._incoming.close();
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => null;
}
