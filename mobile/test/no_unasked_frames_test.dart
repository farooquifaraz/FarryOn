/// No picture leaves this phone unless somebody asked for one.
///
/// The rule, in the order it is easiest to break:
///
/// 1. A session on its own sends nothing. The camera is not even opened.
/// 2. Asking a question about the view ("what is this?", the Scan button)
///    sends exactly ONE frame and closes the camera again.
/// 3. Turning the camera on by hand streams, because that is a request to be
///    watched — and it is the only way to get there.
///
/// Rule 2 is the one that was wrong. `grabFrame` used to switch the camera on
/// and leave it running, so a single tap on Scan bought a frame a second for
/// the rest of the conversation: thousands of pictures of whatever the phone
/// happened to be facing, none of them asked for, every one of them billed.
library;

import 'dart:typed_data';

import 'package:farryon/capture/device_registry.dart';
import 'package:farryon/core/config.dart';
import 'package:farryon/data/live_client.dart';
import 'package:farryon/protocol/frames.dart';
import 'package:farryon/protocol/protocol.dart';
import 'package:farryon/state/live_controller.dart';
import 'package:flutter_test/flutter_test.dart';

import 'live_client_test.dart' show FakeChannel;
import 'live_controller_test.dart' show FakeCaptureSource, FakePcmPlayer, GrantingPermissions;

void main() {
  late FakeChannel fake;
  late FakeCaptureSource source;
  late DeviceRegistry registry;
  late LiveController controller;

  setUp(() {
    fake = FakeChannel();
    source = FakeCaptureSource();
    registry = DeviceRegistry(factory: (_) => source);
    controller = LiveController(
      config: const AppConfig(host: 'h', port: 8000, secure: false),
      registry: registry,
      player: FakePcmPlayer(),
      permissions: GrantingPermissions(),
      clientFactory: (cfg, deviceInfo) => WebSocketLiveClient(
        config: cfg,
        platform: 'android',
        deviceInfoProvider: deviceInfo,
        channelFactory: (_) => fake,
      ),
      platform: 'android',
    );
  });

  tearDown(() => controller.dispose());

  Future<void> tick() => Future<void>.delayed(Duration.zero);

  /// Frames that actually went out on the wire, which is the only count that
  /// matters — a frame captured and dropped costs nothing at the far end.
  int framesSent() => fake.sentLog
      .whereType<Uint8List>()
      .where((b) => MediaFrame.decode(b).tag == FrameTag.inputVideo)
      .length;

  test('a session by itself sends no frames and opens no camera', () async {
    await controller.connect();
    await tick();
    await Future<void>.delayed(const Duration(milliseconds: 50));
    await tick();

    expect(framesSent(), 0);
    expect(source.videoStarted, isFalse, reason: 'the camera was never opened');
    expect(source.captureOnceCalls, 0, reason: 'and nothing took a picture');
  });

  test('asking about the view takes ONE frame, and does not start streaming',
      () async {
    await controller.connect();
    await tick();

    final frame = await controller.grabFrame();
    await tick();

    expect(frame, isNotNull, reason: 'the question still gets its picture');
    expect(source.captureOnceCalls, 1, reason: 'exactly one, not a stream');
    expect(source.videoStarted, isFalse,
        reason: 'the camera must be closed again, not left running');
    expect(framesSent(), 1, reason: 'one picture asked for, one picture sent');
  });

  test('a second question takes another single frame', () async {
    await controller.connect();
    await tick();

    await controller.grabFrame();
    await tick();
    // The cached frame is deliberately reused while it is the freshest thing
    // there is; clearing it is what a real new question does after the camera
    // has been off. Assert the shape that matters: still not streaming.
    expect(source.videoStarted, isFalse);
    expect(source.captureOnceCalls, 1);
  });

  test('turning the camera on by hand DOES stream — that is the one way in',
      () async {
    await controller.connect();
    await tick();

    await controller.setCameraEnabled(true);
    await tick();
    expect(source.videoStarted, isTrue);

    source.videoCtl.add(Uint8List.fromList([1, 2, 3]));
    source.videoCtl.add(Uint8List.fromList([4, 5, 6]));
    await tick();

    expect(framesSent(), greaterThanOrEqualTo(2),
        reason: 'someone who turns the camera on has asked to be watched');
  });

  test('turning it off stops the frames', () async {
    await controller.connect();
    await tick();
    await controller.setCameraEnabled(true);
    await tick();
    source.videoCtl.add(Uint8List.fromList([1, 2, 3]));
    await tick();
    final sentWhileOn = framesSent();

    await controller.setCameraEnabled(false);
    await tick();
    expect(source.videoStarted, isFalse);

    // Nothing more should reach the wire. The source is a fake, so pushing a
    // frame here is the strongest form of the question: even if something did
    // emit after the camera was turned off, it must not be sent.
    expect(framesSent(), sentWhileOn,
        reason: 'the camera is off — nothing else may go out');
  });

  test('a frame that was asked for gets through while Farry is talking',
      () async {
    // The trap this fell into. Continuous frames are dropped while the
    // assistant speaks — right, because they cannot affect the reply already
    // in flight. But asking "what am I looking at?" makes the model start
    // talking AND ask for a picture, so the rule was throwing away the very
    // frame the tool was blocked on. Measured on an S23 (2026-08-16): the
    // backend waited its full 8 s and timed out while the camera had captured
    // perfectly.
    await controller.connect();
    await tick();

    // The assistant starts speaking, exactly as it does when answering.
    fake.pushJson({'type': 'audio_start'});
    await tick();

    final before = framesSent();
    await controller.grabFrame();
    await tick();

    expect(framesSent(), before + 1,
        reason: 'the picture it asked for must reach the model mid-sentence');
  });

  test('continuous frames are still dropped while Farry is talking', () async {
    // The other half — the rule this must not undo.
    await controller.connect();
    await tick();
    await controller.setCameraEnabled(true);
    await tick();

    fake.pushJson({'type': 'audio_start'});
    await tick();
    final before = framesSent();

    source.videoCtl.add(Uint8List.fromList([1, 2, 3]));
    source.videoCtl.add(Uint8List.fromList([4, 5, 6]));
    await tick();

    expect(framesSent(), before,
        reason: 'a stream nobody is waiting on still waits its turn');
  });
}
