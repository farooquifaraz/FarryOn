import 'dart:convert';

import 'package:farryon/protocol/messages.dart';
import 'package:farryon/protocol/protocol.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('client → server serialization', () {
    test('hello matches PROTOCOL.md shape', () {
      const hello = HelloMessage(
        platform: 'android',
        appVersion: '1.0.0',
        device: DeviceInfo(
          kind: 'phone',
          id: 'phone-default',
          capabilities: ['audio_in', 'video_in', 'audio_out'],
        ),
        resumeId: 'sess-123',
      );
      final json = hello.toJson();

      expect(json['type'], 'hello');
      expect(json['protocolVersion'], kProtocolVersion);
      expect(json['client'], {'platform': 'android', 'appVersion': '1.0.0'});
      expect(json['device'], {
        'kind': 'phone',
        'id': 'phone-default',
        'capabilities': ['audio_in', 'video_in', 'audio_out'],
      });
      expect(json['session'], {'resumeId': 'sess-123'});

      // Must be JSON-encodable.
      expect(() => jsonEncode(json), returnsNormally);
    });

    test('hello without resumeId carries an explicit null', () {
      const hello = HelloMessage(
        platform: 'ios',
        appVersion: '2.0.0',
        device: DeviceInfo(kind: 'glasses', id: 'g1', capabilities: []),
      );
      final session = hello.toJson()['session'] as Map<String, dynamic>;
      expect(session.containsKey('resumeId'), isTrue);
      expect(session['resumeId'], isNull);
    });

    test('hello carries provider when set, omits it when null', () {
      const device = DeviceInfo(kind: 'phone', id: 'p', capabilities: []);
      final withProvider = const HelloMessage(
        platform: 'android',
        appVersion: '1.0.0',
        device: device,
        provider: 'grok',
      ).toJson();
      expect(withProvider['provider'], 'grok');

      final withoutProvider = const HelloMessage(
        platform: 'android',
        appVersion: '1.0.0',
        device: device,
      ).toJson();
      expect(withoutProvider.containsKey('provider'), isFalse);
    });

    test('config declares 16k in / 24k out per the contract', () {
      final json = const ConfigMessage().toJson();
      expect(json['type'], 'config');
      expect(json['audioIn'],
          {'encoding': 'pcm16', 'sampleRate': 16000, 'channels': 1});
      expect(json['videoIn'], {'format': 'jpeg', 'fps': 1, 'maxWidth': 1280});
      expect(json['audioOut'],
          {'encoding': 'pcm16', 'sampleRate': 24000, 'channels': 1});
    });

    test('audio_start / audio_stop / interrupt are bare typed messages', () {
      expect(const AudioStartMessage().toJson(), {'type': 'audio_start'});
      expect(const AudioStopMessage().toJson(), {'type': 'audio_stop'});
      expect(const InterruptMessage().toJson(), {'type': 'interrupt'});
    });

    test('text message', () {
      expect(const TextMessage('hi there').toJson(),
          {'type': 'text', 'text': 'hi there'});
    });

    test('tool_permission message', () {
      expect(
        const ToolPermissionMessage(id: 'call-1', granted: true).toJson(),
        {'type': 'tool_permission', 'id': 'call-1', 'granted': true},
      );
    });

    test('ping carries timestamp t', () {
      expect(const PingMessage(1718764800000).toJson(),
          {'type': 'ping', 't': 1718764800000});
    });
  });

  group('server → client deserialization', () {
    test('ready', () {
      final msg = ServerMessage.fromJson({
        'type': 'ready',
        'sessionId': 'uuid-1',
        'protocolVersion': 1,
        'model': 'gemini-live',
      });
      expect(msg, isA<ReadyMessage>());
      msg as ReadyMessage;
      expect(msg.sessionId, 'uuid-1');
      expect(msg.protocolVersion, 1);
      expect(msg.model, 'gemini-live');
    });

    test('transcript maps the "final" key to isFinal', () {
      final msg = ServerMessage.fromJson({
        'type': 'transcript',
        'role': 'user',
        'text': 'hello world',
        'final': true,
      });
      expect(msg, isA<TranscriptMessage>());
      msg as TranscriptMessage;
      expect(msg.role, 'user');
      expect(msg.isUser, isTrue);
      expect(msg.text, 'hello world');
      expect(msg.isFinal, isTrue);
    });

    test('transcript defaults final to false when missing', () {
      final msg = ServerMessage.fromJson({
        'type': 'transcript',
        'role': 'assistant',
        'text': 'partial',
      }) as TranscriptMessage;
      expect(msg.isFinal, isFalse);
      expect(msg.isAssistant, isTrue);
    });

    test('audio_start / audio_end server events', () {
      expect(ServerMessage.fromJson({'type': 'audio_start'}),
          isA<AudioStartEvent>());
      expect(ServerMessage.fromJson({'type': 'audio_end'}),
          isA<AudioEndEvent>());
    });

    test('tool_call', () {
      final msg = ServerMessage.fromJson({
        'type': 'tool_call',
        'id': 'call-id',
        'name': 'create_note',
        'args': {'text': 'buy milk'},
        'needsPermission': false,
      });
      expect(msg, isA<ToolCallMessage>());
      msg as ToolCallMessage;
      expect(msg.id, 'call-id');
      expect(msg.name, 'create_note');
      expect(msg.args, {'text': 'buy milk'});
      expect(msg.needsPermission, isFalse);
    });

    test('tool_result success', () {
      final msg = ServerMessage.fromJson({
        'type': 'tool_result',
        'id': 'call-id',
        'name': 'create_note',
        'ok': true,
        'result': {'id': 12},
      });
      expect(msg, isA<ToolResultMessage>());
      msg as ToolResultMessage;
      expect(msg.ok, isTrue);
      expect(msg.result, {'id': 12});
    });

    test('state maps to the LiveState enum', () {
      final msg = ServerMessage.fromJson({'type': 'state', 'value': 'speaking'})
          as StateMessage;
      expect(msg.value, LiveState.speaking);
    });

    test('unknown state value falls back to idle', () {
      final msg = ServerMessage.fromJson(
        {'type': 'state', 'value': 'dreaming'},
      ) as StateMessage;
      expect(msg.value, LiveState.idle);
    });

    test('error', () {
      final msg = ServerMessage.fromJson({
        'type': 'error',
        'code': 'rate_limited',
        'message': 'slow down',
        'fatal': false,
      }) as ErrorMessage;
      expect(msg.code, 'rate_limited');
      expect(msg.message, 'slow down');
      expect(msg.fatal, isFalse);
    });

    test('pong echoes t', () {
      final msg =
          ServerMessage.fromJson({'type': 'pong', 't': 999}) as PongMessage;
      expect(msg.t, 999);
    });

    test('unknown type yields UnknownServerMessage, not a throw', () {
      final msg = ServerMessage.fromJson({'type': 'future_event', 'x': 1});
      expect(msg, isA<UnknownServerMessage>());
      msg as UnknownServerMessage;
      expect(msg.type, 'future_event');
      expect(msg.raw['x'], 1);
    });

    test('round-trips through a JSON string (as off the wire)', () {
      const wire = '{"type":"transcript","role":"assistant",'
          '"text":"hi","final":false}';
      final decoded = jsonDecode(wire) as Map<String, dynamic>;
      final msg = ServerMessage.fromJson(decoded) as TranscriptMessage;
      expect(msg.text, 'hi');
      expect(msg.isAssistant, isTrue);
    });
  });

  group('LiveState wire mapping', () {
    test('fromWire/idle default', () {
      expect(LiveState.fromWire('listening'), LiveState.listening);
      expect(LiveState.fromWire('thinking'), LiveState.thinking);
      expect(LiveState.fromWire('speaking'), LiveState.speaking);
      expect(LiveState.fromWire('idle'), LiveState.idle);
      expect(LiveState.fromWire(null), LiveState.idle);
      expect(LiveState.fromWire('???'), LiveState.idle);
    });

    test('wire getter is the enum name', () {
      expect(LiveState.speaking.wire, 'speaking');
    });
  });
}
