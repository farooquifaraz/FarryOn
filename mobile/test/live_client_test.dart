import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:farryon/core/config.dart';
import 'package:farryon/data/live_client.dart';
import 'package:farryon/protocol/frames.dart';
import 'package:farryon/protocol/messages.dart';
import 'package:farryon/protocol/protocol.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:stream_channel/stream_channel.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

/// A controllable in-memory [WebSocketChannel] for tests: records everything the
/// client sends and lets the test push frames or simulate a drop.
class FakeChannel with StreamChannelMixin<dynamic> implements WebSocketChannel {
  FakeChannel()
      : _incoming = StreamController<dynamic>(),
        sentLog = <dynamic>[] {
    _sink = _FakeSink(this);
  }

  final StreamController<dynamic> _incoming;
  final List<dynamic> sentLog;
  late final _FakeSink _sink;
  bool closed = false;

  /// Push a server→client message into the client.
  void pushJson(Map<String, dynamic> json) => _incoming.add(jsonEncode(json));
  void pushBinary(Uint8List bytes) => _incoming.add(bytes);

  /// Simulate the socket dropping.
  void drop() {
    if (!_incoming.isClosed) _incoming.close();
  }

  @override
  Stream<dynamic> get stream => _incoming.stream;

  @override
  WebSocketSink get sink => _sink;

  @override
  int? get closeCode => closed ? 1000 : null;

  @override
  String? get closeReason => null;

  @override
  String? get protocol => null;

  @override
  Future<void> get ready => Future.value();
}

class _FakeSink implements WebSocketSink {
  _FakeSink(this._channel);
  final FakeChannel _channel;

  @override
  void add(dynamic data) => _channel.sentLog.add(data);

  @override
  Future<void> close([int? closeCode, String? closeReason]) async {
    _channel.closed = true;
    if (!_channel._incoming.isClosed) await _channel._incoming.close();
  }

  @override
  void addError(Object error, [StackTrace? stackTrace]) {}

  @override
  Future<void> addStream(Stream<dynamic> stream) async {
    await for (final e in stream) {
      add(e);
    }
  }

  @override
  Future<void> get done => Future.value();
}

AppConfig _config() =>
    const AppConfig(host: 'localhost', port: 8000, secure: false);

DeviceInfo _device() => const DeviceInfo(
      kind: 'phone',
      id: 'test-device',
      capabilities: ['audio_in', 'video_in', 'audio_out'],
    );

void main() {
  group('WebSocketLiveClient handshake', () {
    test('sends hello + config on connect', () async {
      final fake = FakeChannel();
      final client = WebSocketLiveClient(
        config: _config(),
        platform: 'android',
        deviceInfoProvider: _device,
        channelFactory: (_) => fake,
      );
      addTearDown(client.dispose);

      client.start();
      await Future<void>.delayed(Duration.zero);

      expect(fake.sentLog.length, greaterThanOrEqualTo(2));
      final hello = jsonDecode(fake.sentLog[0] as String);
      final config = jsonDecode(fake.sentLog[1] as String);
      expect(hello['type'], 'hello');
      expect(hello['protocolVersion'], kProtocolVersion);
      expect(hello['device']['id'], 'test-device');
      expect(config['type'], 'config');
      expect(config['audioIn']['sampleRate'], 16000);
      expect(config['audioOut']['sampleRate'], 24000);
    });

    test('ready transitions status to connected and captures resumeId',
        () async {
      final fake = FakeChannel();
      final client = WebSocketLiveClient(
        config: _config(),
        platform: 'android',
        deviceInfoProvider: _device,
        channelFactory: (_) => fake,
      );
      addTearDown(client.dispose);

      final statuses = <ConnectionStatus>[];
      client.status.listen(statuses.add);

      client.start();
      await Future<void>.delayed(Duration.zero);
      fake.pushJson({
        'type': 'ready',
        'sessionId': 'sess-abc',
        'protocolVersion': 1,
        'model': 'gemini-live',
      });
      await Future<void>.delayed(Duration.zero);

      expect(client.currentStatus, ConnectionStatus.connected);
      expect(client.resumeId, 'sess-abc');
      expect(statuses, contains(ConnectionStatus.connected));
    });
  });

  group('media frames', () {
    test('sendAudio emits a 0x01 frame, sendVideo a 0x02 frame', () async {
      final fake = FakeChannel();
      final client = WebSocketLiveClient(
        config: _config(),
        platform: 'android',
        deviceInfoProvider: _device,
        channelFactory: (_) => fake,
      );
      addTearDown(client.dispose);
      client.start();
      await Future<void>.delayed(Duration.zero);

      client.sendAudio(Uint8List.fromList([1, 2, 3, 4]), timestampMs: 5);
      client.sendVideo(Uint8List.fromList([9, 9]), timestampMs: 6);

      final binary = fake.sentLog.whereType<Uint8List>().toList();
      expect(binary.length, 2);

      final audio = MediaFrame.decode(binary[0]);
      expect(audio.tag, FrameTag.inputAudio);
      expect(audio.timestampMs, 5);
      expect(audio.payload, equals(Uint8List.fromList([1, 2, 3, 4])));

      final video = MediaFrame.decode(binary[1]);
      expect(video.tag, FrameTag.inputVideo);
      expect(video.timestampMs, 6);
    });

    test('incoming OUTPUT_AUDIO binary is decoded onto frames stream',
        () async {
      final fake = FakeChannel();
      final client = WebSocketLiveClient(
        config: _config(),
        platform: 'android',
        deviceInfoProvider: _device,
        channelFactory: (_) => fake,
      );
      addTearDown(client.dispose);

      final frames = <DecodedFrame>[];
      client.frames.listen(frames.add);
      client.start();
      await Future<void>.delayed(Duration.zero);

      fake.pushBinary(MediaFrame.encode(
        tag: FrameTag.outputAudio,
        timestampMs: 100,
        payload: Uint8List.fromList([7, 7, 7]),
      ));
      await Future<void>.delayed(Duration.zero);

      expect(frames.single.tag, FrameTag.outputAudio);
      expect(frames.single.payload, equals(Uint8List.fromList([7, 7, 7])));
    });
  });

  group('events stream', () {
    test('decoded server messages are forwarded', () async {
      final fake = FakeChannel();
      final client = WebSocketLiveClient(
        config: _config(),
        platform: 'android',
        deviceInfoProvider: _device,
        channelFactory: (_) => fake,
      );
      addTearDown(client.dispose);

      final events = <ServerMessage>[];
      client.events.listen(events.add);
      client.start();
      await Future<void>.delayed(Duration.zero);

      fake.pushJson({
        'type': 'transcript',
        'role': 'assistant',
        'text': 'hello',
        'final': true,
      });
      await Future<void>.delayed(Duration.zero);

      final t = events.whereType<TranscriptMessage>().single;
      expect(t.text, 'hello');
      expect(t.isFinal, isTrue);
    });
  });

  group('reconnect', () {
    test('reconnects after a drop and replays resumeId in hello', () async {
      FakeChannel? latest;
      var connectCount = 0;
      final client = WebSocketLiveClient(
        config: _config(),
        platform: 'android',
        deviceInfoProvider: _device,
        channelFactory: (_) {
          connectCount++;
          return latest = FakeChannel();
        },
      );
      addTearDown(client.dispose);

      client.start();
      await Future<void>.delayed(Duration.zero);
      expect(connectCount, 1);

      // First connection becomes ready (so resumeId is captured and backoff
      // resets to ~0–0.5s jittered).
      latest!.pushJson({
        'type': 'ready',
        'sessionId': 'sess-1',
        'protocolVersion': 1,
      });
      await Future<void>.delayed(Duration.zero);
      expect(client.resumeId, 'sess-1');

      // Drop the socket; the client should schedule a reconnect.
      latest!.drop();
      await Future<void>.delayed(Duration.zero);
      expect(client.currentStatus, ConnectionStatus.reconnecting);

      // Wait out the (jittered, ≤500ms after a reset) backoff.
      await Future<void>.delayed(const Duration(milliseconds: 700));
      expect(connectCount, 2, reason: 'should have reconnected');

      // The new hello must carry the previous session id.
      final hello = jsonDecode(latest!.sentLog[0] as String);
      expect(hello['type'], 'hello');
      expect(hello['session']['resumeId'], 'sess-1');
    });

    test('stop() prevents further reconnects', () async {
      var connectCount = 0;
      FakeChannel? latest;
      final client = WebSocketLiveClient(
        config: _config(),
        platform: 'android',
        deviceInfoProvider: _device,
        channelFactory: (_) {
          connectCount++;
          return latest = FakeChannel();
        },
      );
      addTearDown(client.dispose);

      client.start();
      await Future<void>.delayed(Duration.zero);
      await client.stop();
      expect(client.currentStatus, ConnectionStatus.disconnected);

      latest!.drop();
      await Future<void>.delayed(const Duration(milliseconds: 700));
      expect(connectCount, 1, reason: 'no reconnect after stop()');
    });
  });
}
