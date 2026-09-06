import 'package:farryon/core/music_controller.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

/// The Dart -> platform contract for music.
///
/// `MusicController` is the only thing standing between a tool result and the
/// phone's player, and everything it does is fire-and-forget — a wrong method
/// name or argument would fail silently in the one place nobody is watching.
/// These tests read the calls back off the channel.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const channel = MethodChannel('com.farryon/music');
  final calls = <MethodCall>[];
  Object? reply = true;

  setUp(() {
    calls.clear();
    reply = true;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
      calls.add(call);
      if (reply is Exception) throw reply as Exception;
      return reply;
    });
  });

  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
  });

  group('play', () {
    test('sends the query to playFromSearch', () async {
      expect(await MusicController.play('Arijit Singh'), isTrue);
      expect(calls.single.method, 'playFromSearch');
      expect(calls.single.arguments['query'], 'Arijit Singh');
    });

    test('"default" means let the phone choose, so no app is named', () async {
      await MusicController.play('jazz', app: 'default');
      expect(calls.single.arguments['app'], isNull);
    });

    test('a named app is passed through', () async {
      await MusicController.play('jazz', app: 'spotify');
      expect(calls.single.arguments['app'], 'spotify');
    });

    test('an empty query never reaches the platform', () async {
      expect(await MusicController.play('   '), isFalse);
      expect(calls, isEmpty);
    });

    test('no music app installed reports false, it does not throw', () async {
      reply = false;
      expect(await MusicController.play('jazz'), isFalse);
    });
  });

  group('transport', () {
    test('a command goes out as a media key', () async {
      expect(await MusicController.command('next'), isTrue);
      expect(calls.single.method, 'mediaKey');
      expect(calls.single.arguments['command'], 'next');
    });

    test('a platform failure is swallowed, never thrown at the session',
        () async {
      reply = Exception('boom');
      expect(await MusicController.command('pause'), isFalse);
    });
  });
}
