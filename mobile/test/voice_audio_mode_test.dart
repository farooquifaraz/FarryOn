import 'package:farryon/playback/voice_audio_mode.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const channel = MethodChannel('com.farryon/audio_mode');
  final calls = <String>[];

  void stub(String? reply, {bool throws = false}) {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
      calls.add(call.method);
      if (throws) throw PlatformException(code: 'boom');
      return reply;
    });
  }

  setUp(calls.clear);
  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
  });

  test('applied on the speakerphone route', () async {
    stub('applied');
    final mode = VoiceAudioMode();
    expect(await mode.enter(), 'applied');
    expect(mode.isActive, isTrue);
    expect(calls, ['enterVoiceMode']);
  });

  test('glasses / earbuds leave the media path untouched', () async {
    // The native side refuses the switch when audio is going to Bluetooth or a
    // wired headset: communication mode would drag the glasses' A2DP down to
    // narrowband SCO. The Dart side must not believe it is active.
    stub('skipped_external_route');
    final mode = VoiceAudioMode();
    expect(await mode.enter(), 'skipped_external_route');
    expect(mode.isActive, isFalse);
  });

  test('a platform failure never escapes and never claims success', () async {
    stub(null, throws: true);
    final mode = VoiceAudioMode();
    expect(await mode.enter(), 'unavailable');
    expect(mode.isActive, isFalse);
    await mode.exit(); // must not throw either
  });

  test('exit clears the flag and is safe to repeat', () async {
    stub('applied');
    final mode = VoiceAudioMode();
    await mode.enter();
    stub(null);
    calls.clear();
    await mode.exit();
    expect(mode.isActive, isFalse);
    await mode.exit();
    expect(calls, ['exitVoiceMode', 'exitVoiceMode']);
  });
}
