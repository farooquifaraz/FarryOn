// The mic watchdog and the truthful "hearing" flag.
//
// On 2026-09-08 the phone mic went quiet after music playback and stayed
// quiet for ten minutes while the chip said "listening": no chunk reached the
// server, nothing noticed, nothing restarted, nobody was told. These pin the
// three promises made in response: a silent phone mic is restarted, a mic
// that stays silent is reported, and the screen only ever says the mic is
// hearing when audio is really going out.
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:fake_async/fake_async.dart';
import 'package:farryon/capture/device_registry.dart';
import 'package:farryon/core/config.dart';
import 'package:farryon/data/live_client.dart';
import 'package:farryon/playback/audio_focus.dart';
import 'package:farryon/state/live_controller.dart';
import 'package:farryon/state/live_state.dart';
import 'package:flutter_test/flutter_test.dart';

import 'live_client_test.dart' show FakeChannel;
import 'live_controller_test.dart'
    show FakeCaptureSource, FakePcmPlayer, GrantingPermissions;

/// A phone mic that remembers how many times it was started and stopped.
class CountingSource extends FakeCaptureSource {
  int starts = 0;
  int stops = 0;


  @override
  Future<void> startAudio() async {
    starts++;
    await super.startAudio();
  }

  @override
  Future<void> stopAudio() async {
    stops++;
    await super.stopAudio();
  }
}

/// Records focus requests instead of talking to the platform.
class FakeFocus extends AudioFocus {
  int ducks = 0;
  int releases = 0;
  bool musicActive = false;
  bool _held = false;

  @override
  bool get isHeld => _held;

  @override
  Future<String> duck() async {
    ducks++;
    _held = true;
    return 'granted';
  }

  @override
  Future<void> release() async {
    if (_held) releases++;
    _held = false;
  }

  @override
  Future<bool> isMusicActive() async => musicActive;
}

/// One 20 ms chunk of speech-loud PCM16 (a tone; RMS ≈ amplitude / √2).
Uint8List loud({double amplitude = 8000}) {
  final data = Int16List(320);
  for (var i = 0; i < data.length; i++) {
    data[i] = (amplitude * math.sin(i * 0.3)).round();
  }
  return data.buffer.asUint8List();
}

void main() {
  late FakeChannel fake;
  late CountingSource source;
  late FakePcmPlayer player;
  late DeviceRegistry registry;
  late FakeFocus focus;

  LiveController build({AppConfig? config}) {
    fake = FakeChannel();
    source = CountingSource();
    player = FakePcmPlayer();
    focus = FakeFocus();
    registry = DeviceRegistry(factory: (_) => source);
    return LiveController(
      config: config ?? const AppConfig(host: 'h', port: 8000, secure: false),
      registry: registry,
      player: player,
      permissions: GrantingPermissions(),
      clientFactory: (cfg, deviceInfo) => WebSocketLiveClient(
        config: cfg,
        platform: 'android',
        deviceInfoProvider: deviceInfo,
        channelFactory: (_) => fake,
      ),
      platform: 'android',
      audioFocus: focus,
    );
  }

  /// Connect (with the server's `ready`) and open the mic inside a fake clock.
  void open(FakeAsync async, LiveController ctl) {
    ctl.connect();
    async.flushMicrotasks();
    fake.pushJson({'type': 'ready'});
    async.flushMicrotasks();
    ctl.startListening();
    async.flushMicrotasks();
    expect(ctl.state.micOpen, isTrue);
    expect(ctl.state.micHealth, MicHealth.ok);
  }

  /// Let fake time pass one second at a time, answering the client's
  /// heartbeat as a live server would — otherwise a long wait reads as a
  /// dead link and the client reconnects, which is not what is under test.
  void elapse(FakeAsync async, int seconds) {
    for (var i = 0; i < seconds; i++) {
      async.elapse(const Duration(seconds: 1));
      fake.pushJson({'type': 'pong'});
      async.flushMicrotasks();
    }
  }

  bool hasNotice(LiveController ctl, String fragment) => ctl.state.transcripts
      .any((t) => t.role == 'notice' && t.text.contains(fragment));

  test('a phone mic that goes quiet is restarted, and healthy again on audio',
      () {
    fakeAsync((async) {
      final ctl = build();
      open(async, ctl);
      expect(source.starts, 1);

      // Two silent seconds are tolerated.
      async.elapse(const Duration(seconds: 2));
      expect(source.starts, 1);
      expect(ctl.state.micHealth, MicHealth.ok);

      // The third is not: stop + start, and the screen says so.
      async.elapse(const Duration(seconds: 1));
      async.flushMicrotasks();
      expect(source.stops, 1);
      expect(source.starts, 2);
      expect(ctl.state.micHealth, MicHealth.restarting);

      // Audio flows again → healthy, no notice needed.
      source.audioCtl.add(Uint8List.fromList([1, 2]));
      async.flushMicrotasks();
      expect(ctl.state.micHealth, MicHealth.ok);
      expect(hasNotice(ctl, 'unable to listen'), isFalse);
      ctl.dispose();
    });
  });

  test('audio keeps the watchdog quiet', () {
    fakeAsync((async) {
      final ctl = build();
      open(async, ctl);
      for (var s = 0; s < 10; s++) {
        elapse(async, 1);
        source.audioCtl.add(Uint8List.fromList([0, 0]));
        async.flushMicrotasks();
      }
      expect(source.starts, 1, reason: 'a live mic is never restarted');
      expect(ctl.state.micHealth, MicHealth.ok);
      ctl.dispose();
    });
  });

  test('a mic that stays silent is reported once, then left to the user', () {
    fakeAsync((async) {
      final ctl = build();
      open(async, ctl);

      // Three restarts, three seconds apart.
      for (var attempt = 1; attempt <= 3; attempt++) {
        elapse(async, 3);
        expect(source.starts, 1 + attempt);
      }
      expect(hasNotice(ctl, 'unable to listen'), isFalse,
          reason: 'still trying — nothing to tell yet');

      // Still nothing: give up, say so, stop restarting.
      elapse(async, 3);
      expect(ctl.state.micHealth, MicHealth.silent);
      expect(hasNotice(ctl, 'unable to listen'), isTrue);
      final startsWhenGivenUp = source.starts;
      elapse(async, 30);
      expect(source.starts, startsWhenGivenUp);
      expect(
          ctl.state.transcripts
              .where((t) => t.text.contains('unable to listen'))
              .length,
          1,
          reason: 'one notice, not one every three seconds');

      // The user toggles the mic: a fresh start, watched again.
      ctl.stopListening();
      async.flushMicrotasks();
      ctl.startListening();
      async.flushMicrotasks();
      expect(ctl.state.micHealth, MicHealth.ok);
      elapse(async, 3);
      expect(ctl.state.micHealth, MicHealth.restarting);
      ctl.dispose();
    });
  });

  test('the glasses mic is push-to-talk: silence is not a fault', () {
    fakeAsync((async) {
      final ctl = build();
      ctl.connect();
      async.flushMicrotasks();
      fake.pushJson({'type': 'ready'});
      async.flushMicrotasks();
      ctl.setAudioDevice(CaptureDeviceKind.glasses);
      async.flushMicrotasks();
      ctl.startListening();
      async.flushMicrotasks();
      final startsAfterOpen = source.starts;
      elapse(async, 30);
      expect(source.starts, startsAfterOpen);
      expect(ctl.state.micHealth, MicHealth.ok);
      ctl.dispose();
    });
  });

  test('"hearing" is the gate being open, and ends with the mic', () {
    fakeAsync((async) {
      final ctl = build();
      open(async, ctl);
      expect(ctl.state.hearing, isFalse);

      source.audioCtl.add(Uint8List.fromList([0, 0]));
      async.flushMicrotasks();
      expect(ctl.state.hearing, isFalse, reason: 'silence is not hearing');

      source.audioCtl.add(loud());
      async.flushMicrotasks();
      expect(ctl.state.hearing, isTrue);

      ctl.stopListening();
      async.flushMicrotasks();
      expect(ctl.state.hearing, isFalse);
      ctl.dispose();
    });
  });

  test('while Farry speaks, her own echo is not "hearing you"', () {
    fakeAsync((async) {
      final ctl = build();
      open(async, ctl);
      fake.pushJson({'type': 'audio_start'});
      async.flushMicrotasks();
      source.audioCtl.add(loud());
      async.flushMicrotasks();
      expect(ctl.state.hearing, isFalse);
      // ...but the chunk still proved the mic alive.
      async.elapse(const Duration(seconds: 2));
      expect(ctl.state.micHealth, MicHealth.ok);
      ctl.dispose();
    });
  });

  test('music is not touched unless the user switched ducking on', () {
    fakeAsync((async) {
      final ctl = build();
      open(async, ctl);
      source.audioCtl.add(loud());
      async.flushMicrotasks();
      fake.pushJson({'type': 'audio_start'});
      async.flushMicrotasks();
      expect(focus.ducks, 0);
      ctl.dispose();
    });
  });

  test('with ducking on: held from the first word, released after the reply',
      () {
    fakeAsync((async) {
      final ctl = build(
        config: const AppConfig(
          host: 'h',
          port: 8000,
          secure: false,
          duckMusicForVoice: true,
        ),
      );
      open(async, ctl);

      source.audioCtl.add(loud());
      async.flushMicrotasks();
      expect(focus.ducks, 1, reason: 'the user is speaking');

      fake.pushJson({'type': 'audio_start'});
      async.flushMicrotasks();
      fake.pushJson({'type': 'audio_end'});
      async.flushMicrotasks();
      expect(focus.releases, 0, reason: 'her voice is still draining');

      // audio_end → playback drain (0 bytes) + tail 800 ms → over → +1.5 s.
      async.elapse(const Duration(seconds: 3));
      async.flushMicrotasks();
      expect(focus.releases, 1);
      expect(focus.isHeld, isFalse);
      ctl.dispose();
    });
  });

  test('muting the mic gives the music back at once', () {
    fakeAsync((async) {
      final ctl = build(
        config: const AppConfig(
          host: 'h',
          port: 8000,
          secure: false,
          duckMusicForVoice: true,
        ),
      );
      open(async, ctl);
      source.audioCtl.add(loud());
      async.flushMicrotasks();
      expect(focus.isHeld, isTrue);
      ctl.stopListening();
      async.flushMicrotasks();
      expect(focus.isHeld, isFalse);
      ctl.dispose();
    });
  });
}
