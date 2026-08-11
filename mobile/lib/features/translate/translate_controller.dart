import 'dart:async';
import 'dart:typed_data';

import '../../capture/capture_source.dart';
import '../../capture/device_registry.dart';
import '../../core/config.dart';
import '../../core/logger.dart';
import '../../core/notifications.dart';
import '../../data/live_client.dart';
import '../../playback/pcm_player.dart';
import '../../playback/voice_audio_mode.dart';
import '../../protocol/frames.dart';
import '../../protocol/messages.dart';
import '../../protocol/protocol.dart';
import '../../state/permissions.dart';
import '../glasses_lab/bridge/glasses_channel.dart';
import 'translate_languages.dart';
import 'translate_state.dart';

/// Drives one live-translation session.
///
/// **Why this is not a mode inside `LiveController`.** The assistant path is
/// deliberately half-duplex: `_ttsActive` and `PcmPlayer.isPlayingWithin` mute
/// the microphone whenever anything is playing, and user transcripts arriving
/// during playback are discarded as echo. Continuous translation is full-duplex
/// by definition — the room keeps talking while the translation is spoken — so
/// every one of those guards would have to be bypassed. Rather than thread a
/// flag through logic that exists to stop the assistant hearing itself, this
/// owns a separate socket and a separate player, and the live session is
/// disconnected while it runs. Nothing in `live_controller.dart` changes.
///
/// **On feedback, this doc used to be wrong.** It said the platform AEC made
/// full duplex viable and no echo guard was needed. On a phone at speaker
/// volume it is not: one English sentence came back as fourteen separate
/// translations, each the previous one looping in through the microphone
/// (device-proven 2026-08-10). `echoTargetLanguage: false` did not save it
/// either — a reverberated copy is not reliably detected as the target
/// language.
///
/// The session also never asked for the **voice audio path**, so the
/// translation played on the media path where the canceller has no reference
/// signal at all. That is now fixed (`voice_audio_mode.dart`, which the
/// assistant has used since August) — but it was NOT the answer either. With
/// the voice path claimed and the hold removed, the loop came straight back:
/// the "heard" pane filled with our own Hindi, labelled English. At
/// loudspeaker volume the platform canceller does not get close enough.
///
/// So on the phone's speaker the microphone really is held while we talk, and
/// that costs speech — whatever is said during the translation is lost, which
/// on continuous speech chops the transcript into fragments. The user is told
/// so, because otherwise it reads as a broken feature. The two ways out cost
/// nothing: earphones or the glasses remove the acoustic path, and
/// captions-only plays nothing at all.
///
/// Framework-agnostic (no Riverpod import) so it can be unit-tested directly,
/// matching `LiveController`.
class TranslateController {
  TranslateController({
    required AppConfig config,
    required DeviceRegistry registry,
    required PcmPlayer player,
    required PermissionsService permissions,
    GlassesBridgeApi? glasses,
    VoiceAudioMode? voiceAudioMode,
    WebSocketLiveClient Function(AppConfig, TranslateSessionConfig, DeviceInfo Function())?
        clientFactory,
    Duration? glassesGraceWindow,
  })  : _config = config,
        _voiceAudioMode = voiceAudioMode ?? VoiceAudioMode(),
        _registry = registry,
        _player = player,
        _permissions = permissions,
        _glasses = glasses,
        _glassesGraceWindow = glassesGraceWindow ?? _defaultGlassesGrace,
        _clientFactory = clientFactory ?? _defaultClientFactory;

  static final _log = Logger('TranslateController');

  /// Oldest turns are dropped past this. A translate session can run for an
  /// hour; an unbounded list would make every append O(n) and eventually
  /// exhaust memory. The transcript on screen is a live view, not the record.
  static const int maxTurns = 200;

  /// How long after our own translated audio stops before the microphone is
  /// trusted again.
  ///
  /// Covers the room's acoustic tail, not just the playback itself: what loops
  /// is the reverberated copy that arrives slightly late. Shorter than the
  /// assistant's 800 ms because a translator must return to the room quickly —
  /// long enough to break the loop, short enough not to swallow the reply.
  static const Duration _echoTail = Duration(milliseconds: 400);

  /// How long the microphone may deliver nothing at all before we conclude it
  /// is gone rather than merely pointed at a quiet room.
  ///
  /// Generous on purpose: the capture source emits continuously — silence
  /// arrives as silent chunks — so a real gap this long means the stream has
  /// stopped, not that nobody is speaking. Too short and a pause in a meeting
  /// would end the session; too long and a phone call leaves the screen
  /// pretending to listen.
  static const Duration _micSilenceLimit = Duration(seconds: 20);

  static WebSocketLiveClient _defaultClientFactory(
    AppConfig cfg,
    TranslateSessionConfig tx,
    DeviceInfo Function() deviceInfo,
  ) =>
      WebSocketLiveClient(
        config: cfg,
        platform: 'android',
        deviceInfoProvider: deviceInfo,
        translate: tx,
      );

  AppConfig _config;
  final DeviceRegistry _registry;
  final PcmPlayer _player;
  final PermissionsService _permissions;
  final GlassesBridgeApi? _glasses;
  final WebSocketLiveClient Function(
      AppConfig, TranslateSessionConfig, DeviceInfo Function()) _clientFactory;

  final _stateController = StreamController<TranslateState>.broadcast();
  Stream<TranslateState> get stateStream => _stateController.stream;

  TranslateState _state = const TranslateState();
  TranslateState get state => _state;

  WebSocketLiveClient? _client;
  StreamSubscription<ServerMessage>? _eventSub;
  StreamSubscription<DecodedFrame>? _frameSub;
  StreamSubscription<ConnectionStatus>? _statusSub;
  StreamSubscription<Uint8List>? _audioSub;
  StreamSubscription<GlassesLabEvent>? _glassesSub;

  /// Running while the glasses are away and might still come back.
  Timer? _glassesGrace;

  /// How long a glasses drop is treated as a blip rather than the end.
  ///
  /// The observed reconnect took eleven seconds. Thirty gives that room twice
  /// over without leaving someone staring at a held session for a minute when
  /// the glasses are genuinely off. Injectable only so a test need not sit
  /// through it.
  static const Duration _defaultGlassesGrace = Duration(seconds: 30);
  final Duration _glassesGraceWindow;
  CaptureSource? _audioSource;
  Timer? _micWatchdog;
  DateTime? _lastChunkAt;
  final VoiceAudioMode _voiceAudioMode;

  /// Whether the glasses are connected right now. Seeded when the screen opens
  /// and kept current from the bridge's own connection events.
  bool _glassesConnected = false;

  /// True when the translated audio comes out somewhere that cannot loop back
  /// into the microphone — a headset, earbuds, or the glasses. Reported by the
  /// platform when it declines to switch paths, which is a far better signal
  /// than guessing from what we happen to know is connected.
  bool _outputIsInEar = false;

  bool _disposed = false;

  void _emit(TranslateState next) {
    _state = next;
    if (!_stateController.isClosed) _stateController.add(next);
  }

  /// Seed the target language, captions setting and glasses state.
  void primeFromConfig(AppConfig cfg, {bool glassesConnected = false}) {
    _config = cfg;
    _glassesConnected = glassesConnected;
    _emit(_state.copyWith(
      targetLanguage: cfg.translateTargetLanguage,
      captionsOnly: cfg.translateCaptionsOnly,
    ));
  }

  /// Change the language to translate into.
  ///
  /// Mid-session this reconnects upstream — the target is fixed in the setup
  /// message — but the transcript so far is deliberately kept: the user changed
  /// their mind about the output, not about what was already said.
  Future<void> setTargetLanguage(String code) async {
    if (code == _state.targetLanguage) return;
    _emit(_state.copyWith(targetLanguage: code));
    if (_state.isRunning) {
      await _teardownSocket();
      await _openSocket();
    }
  }

  void setCaptionsOnly(bool value) {
    _emit(_state.copyWith(captionsOnly: value));
    if (value) unawaited(_player.flush());
  }

  /// Whether the glasses are connected, which live translation requires.
  ///
  /// Not a limitation to work around — it is the design. The translation has
  /// to come out somewhere that cannot be heard by the microphone that is
  /// listening to the room, or it loops: on the phone's own speaker one
  /// English sentence came back as fourteen translations of itself
  /// (device-proven 2026-08-10), and the only way to stop that on a
  /// loudspeaker was to hold the microphone — which then swallowed whatever
  /// was said during the translation and chopped sentences into fragments.
  ///
  /// With the glasses on, the speaker is in the wearer's ear. Nothing has to
  /// be held, nothing is missed, and the room can keep talking over the
  /// translation — which is the entire point of a simultaneous interpreter.
  ///
  /// The same reasoning is visible in every product that does this: Google
  /// Translate's continuous Listening mode is headphones-only on iOS, and
  /// HeyCyan's own translator reads its audio off the glasses.
  bool get glassesRequired => true;

  /// Begin translating. Returns false when it could not start.
  Future<bool> start() async {
    if (_disposed || _state.isRunning) return true;
    if (!_glassesConnected) {
      _emit(_state.copyWith(
        status: TranslateStatus.idle,
        error: 'Connect your glasses first. The translation plays in your ear '
            'so it never loops back into the microphone.',
      ));
      return false;
    }
    final outcome = await _permissions.requestMicrophone();
    if (outcome != PermissionOutcome.granted) {
      _emit(_state.copyWith(
        status: TranslateStatus.idle,
        error: outcome == PermissionOutcome.permanentlyDenied
            ? 'Microphone access is blocked. Turn it on in Settings.'
            : 'Live translation needs the microphone.',
      ));
      return false;
    }
    _emit(_state.copyWith(
      status: TranslateStatus.starting,
      startedAt: DateTime.now(),
      turns: const [],
      // Both cleared: a "the glasses disconnected" notice or a quota warning
      // from the last session has nothing to say about this one, and stale
      // banners are how a screen starts lying.
      clearError: true,
      clearNotice: true,
    ));
    await _player.initialize();
    // BEFORE anything plays. The platform echo canceller is attached to our
    // microphone but can only subtract our own output when capture and
    // playback share the voice path — see voice_audio_mode.dart. Skipping this
    // is what turned one English sentence into fourteen looping translations
    // (device-proven 2026-08-10): the audio went out on the media path, the
    // canceller had no reference, and the microphone heard it all.
    final route = await _voiceAudioMode.enter();
    // The platform declines to touch the route when sound is already going to a
    // headset, earbuds or the glasses — which is also exactly the case where
    // there is no acoustic path back into the microphone at all.
    _outputIsInEar = route == 'skipped_external_route';
    _log.info('translate audio route: $route');
    if (!_outputIsInEar && !_state.captionsOnly) {
      // Say it before it happens. On the loudspeaker the microphone has to be
      // held while the translation plays, so continuous speech comes out in
      // fragments — which reads as a broken feature unless you were told.
      _emit(_state.copyWith(
        notice: 'On the phone speaker, anything said while the translation '
            'plays is missed. Use earphones, the glasses, or "Text only" to '
            'hear everything.',
      ));
    }
    _watchGlasses();
    await _openSocket();
    unawaited(_showNotification());
    return true;
  }

  /// Stop translating and release the microphone.
  Future<void> stop() async {
    // Cancel first: a pending grace timer would otherwise fire into a session
    // that has already ended and paint a "glasses disconnected" error over it.
    _glassesGrace?.cancel();
    _glassesGrace = null;
    if (!_state.isRunning && _client == null) return;
    await _stopAudio();
    await _teardownSocket();
    await _player.flush();
    await _player.stop();
    await _voiceAudioMode.exit();
    unawaited(Notifications.clearActivity());
    _emit(_state.copyWith(status: TranslateStatus.stopped));
  }

  // -- Socket ---------------------------------------------------------------

  Future<void> _openSocket() async {
    final tx = TranslateSessionConfig(
      targetLanguage:
          _state.targetLanguage.isEmpty ? 'en' : _state.targetLanguage,
    );
    final client = _clientFactory(_config, tx, () => _deviceInfo());
    _client = client;

    _eventSub = client.events.listen(_onServerMessage);
    _frameSub = client.frames.listen(_onFrame);
    _statusSub = client.status.listen(_onStatus);
    client.start();
  }

  Future<void> _teardownSocket() async {
    await _eventSub?.cancel();
    await _frameSub?.cancel();
    await _statusSub?.cancel();
    _eventSub = null;
    _frameSub = null;
    _statusSub = null;
    final client = _client;
    _client = null;
    await client?.dispose();
  }

  DeviceInfo _deviceInfo() {
    final source = _registry.audioSource;
    return DeviceInfo(
      kind: _registry.audioKind.name,
      id: source.info.id,
      capabilities: const ['audio_in', 'audio_out'],
    );
  }

  void _onStatus(ConnectionStatus status) {
    switch (status) {
      case ConnectionStatus.connected:
        // Audio only starts once the server is ready: frames sent before then
        // are discarded by the client, so starting the mic earlier would drop
        // the first thing anyone says.
        _emit(_state.copyWith(
          status: TranslateStatus.listening,
          clearError: true,
        ));
        unawaited(_startAudio());
      case ConnectionStatus.reconnecting:
      case ConnectionStatus.connecting:
        if (_state.status == TranslateStatus.listening) {
          _emit(_state.copyWith(status: TranslateStatus.reconnecting));
        }
      case ConnectionStatus.disconnected:
        break;
    }
  }

  // -- Audio ----------------------------------------------------------------

  Future<void> _startAudio() async {
    if (_audioSub != null) return;
    final source = _registry.audioSource;
    _audioSource = source;
    try {
      await source.initialize();
    } catch (e) {
      _log.warn('audio source failed to initialize: $e');
      _emit(_state.copyWith(
        error: 'The microphone could not be opened. Try again.',
      ));
      await stop();
      return;
    }
    // No mic GATE, on purpose: a gate tuned to hold back non-speech would clip
    // someone speaking quietly from across a room, which is exactly who a
    // translator exists to hear. But an echo guard is not optional — see
    // [_shouldHoldForEcho].
    _audioSub = source.audio16k.listen(
      (chunk) {
        // Counted before the echo check: the microphone is alive and
        // delivering, which is all the watchdog needs to know. Counting only
        // forwarded chunks would make a long translation look like a dead mic.
        _lastChunkAt = DateTime.now();
        if (_shouldHoldForEcho()) return;
        _client?.sendAudio(chunk);
      },
      // A capture source that ends or errors mid-session is the shape a phone
      // call takes: Android hands the microphone to the dialer and this stream
      // simply stops. Saying so beats a screen that looks like it is listening
      // to a room it can no longer hear.
      onError: (Object e) => _micWentDead('error: $e'),
      onDone: () => _micWentDead('stream ended'),
      cancelOnError: false,
    );
    await source.startAudio();
    _client?.send(const AudioStartMessage());
    _startMicWatchdog();
  }

  /// Whether to withhold this chunk because it is probably our own output.
  ///
  /// **Device-proven 2026-08-10, and it disproved the design.** The class doc
  /// used to say the platform AEC made full duplex viable. On a phone at
  /// speaker volume it does not: one English sentence came back as **fourteen
  /// separate translations**, each one the previous translation looping back in
  /// through the microphone. That is not a bug to fix in code — the microphone
  /// really is hearing a loud, room-reverberated copy of the speaker.
  ///
  /// `echoTargetLanguage: false` was supposed to be a second line of defence
  /// (leaked audio is already in the target language, so the model should stay
  /// silent on it). It is not reliable: a degraded, reverberated copy does not
  /// always get detected as the target language.
  ///
  /// So the microphone is withheld while our own audio plays — but ONLY when
  /// there is an acoustic path back into it. With glasses connected the speaker
  /// is in the wearer's ear, there is no loop, and holding the microphone would
  /// throw away the one thing that makes this feature worth having: hearing the
  /// room continuously while the translation is spoken.
  ///
  /// The other two ways out cost nothing and are worth knowing: captions-only
  /// plays no audio at all, and earphones remove the path the same way glasses
  /// do.
  bool _shouldHoldForEcho() {
    if (_state.captionsOnly) return false; // nothing is playing
    if (_outputIsInEar) return false; // no acoustic path back
    // NOT short-circuited by the echo canceller, though it was tried.
    // Claiming the voice path (MODE_IN_COMMUNICATION + speakerphone) and
    // trusting the platform to subtract our output put the loop straight back:
    // the "heard" pane filled with our own Hindi, labelled as English
    // (device-proven 2026-08-10). At loudspeaker volume the canceller does not
    // get close enough. The voice path is still claimed — it reduces the echo
    // and matches what the assistant does — but it is not a substitute for
    // holding the microphone.
    return _player.isPlayingWithin(_echoTail);
  }

  /// The microphone stopped producing audio while we still expect it to.
  void _micWentDead(String why) {
    if (!_state.isRunning) return;
    _log.warn('microphone stopped: $why');
    _emit(_state.copyWith(
      error: 'The microphone stopped — another app may be using it. '
          'Tap to start again.',
    ));
    unawaited(stop());
  }

  /// Notice a microphone that goes quiet without ever raising an error.
  ///
  /// `onDone`/`onError` cover a stream that ends loudly. A phone call, or the
  /// OS quietly revoking the mic, can instead leave the subscription alive and
  /// simply stop delivering — which looks identical to a silent room until you
  /// notice that a silent room still delivers silence.
  void _startMicWatchdog() {
    _micWatchdog?.cancel();
    _lastChunkAt = DateTime.now();
    _micWatchdog = Timer.periodic(const Duration(seconds: 5), (_) {
      if (!_state.isRunning || _audioSub == null) return;
      final last = _lastChunkAt;
      if (last == null) return;
      if (DateTime.now().difference(last) > _micSilenceLimit) {
        _micWentDead('no audio for ${_micSilenceLimit.inSeconds}s');
      }
    });
  }

  Future<void> _stopAudio() async {
    _micWatchdog?.cancel();
    _micWatchdog = null;
    _lastChunkAt = null;
    await _audioSub?.cancel();
    _audioSub = null;
    final source = _audioSource;
    _audioSource = null;
    if (source != null) {
      await source.stopAudio();
    }
  }

  /// Watch for the glasses dropping while they are the microphone.
  ///
  /// Losing them mid-session would otherwise leave a screen that says
  /// "Listening…" attached to a microphone that no longer exists. Falling back
  /// to the phone keeps the session alive — the user is usually still in the
  /// conversation they were having — and the notice explains why the sound
  /// changed.
  void _watchGlasses() {
    // Deliberately does NOT set `_outputIsInEar` here. The platform already
    // answered that question when it declined to switch audio paths, and its
    // answer covers wired headsets and earbuds too — things the glasses bridge
    // knows nothing about. Overwriting it with "are the glasses connected"
    // threw that away and put the hold back on a headset that never needed it.
    _glassesSub ??= _glasses?.events().listen((event) {
      if (event.type != 'connectionState') return;
      final connected = event.data['state'] == 'connected';
      _glassesConnected = connected;
      // Mid-session the route really does change under us: glasses arriving
      // move the sound into the wearer's ear, glasses leaving bring it back to
      // the phone's speaker and the echo path with it.
      _outputIsInEar = connected;
      if (!_state.isRunning) return;
      if (connected) {
        _onGlassesReturned();
        return;
      }
      _onGlassesLost();
    });
  }

  /// The glasses went away mid-session — wait a little before giving up.
  ///
  /// They come back on their own. Measured on the S23 (2026-08-11):
  /// `disconnected` at 18:18:34, `connected` again at 18:18:45 — **eleven
  /// seconds** — and the session had already ended for good. A blip like that
  /// is ordinary when someone walks between rooms, and ending a conversation
  /// over it is not something the wearer can do anything about.
  ///
  /// The microphone is released for the duration and nothing is played. That
  /// is not politeness: with the glasses gone the sound would come out of the
  /// loudspeaker, into the microphone it is listening with, and back around —
  /// the loop this feature exists to avoid. So the session is *held*, not
  /// continued.
  void _onGlassesLost() {
    if (_glassesGrace != null) return; // already waiting
    unawaited(_stopAudio());
    unawaited(_player.flush());
    _emit(_state.copyWith(
      status: TranslateStatus.reconnecting,
      notice: 'The glasses dropped. Waiting for them to come back…',
    ));
    _glassesGrace = Timer(_glassesGraceWindow, () {
      _glassesGrace = null;
      if (!_state.isRunning && _state.status != TranslateStatus.reconnecting) {
        return;
      }
      _emit(_state.copyWith(
        error: 'The glasses disconnected, so translation stopped. '
            'Reconnect them and start again.',
        clearNotice: true,
      ));
      unawaited(stop());
    });
  }

  /// They came back inside the window: pick up where the conversation was.
  void _onGlassesReturned() {
    final waiting = _glassesGrace;
    if (waiting == null) return;
    waiting.cancel();
    _glassesGrace = null;
    _emit(_state.copyWith(
      status: TranslateStatus.listening,
      clearNotice: true,
    ));
    unawaited(_startAudio());
  }



  // -- Server events --------------------------------------------------------

  void _onServerMessage(ServerMessage msg) {
    switch (msg) {
      case ReadyMessage(:final mode):
        if (mode != 'translate') {
          // The server gave us an assistant. Say so and stop rather than
          // present its answers as translations.
          _log.warn('server accepted mode="$mode", expected translate');
          _emit(_state.copyWith(
            error: 'Live translation is not available on this server.',
          ));
          unawaited(stop());
        }
        return;
      case TranscriptMessage(:final role, :final text, :final isFinal, :final lang):
        _applyTranscript(role: role, text: text, isFinal: isFinal, lang: lang);
      case ErrorMessage(:final code, :final message, :final fatal):
        // A quota heads-up is not a failure. Painting "10 minutes left" in the
        // same red as "translation is unavailable" teaches people to ignore
        // both, and the one that matters is the one they then miss.
        if (!fatal && code == 'quota_warning') {
          _emit(_state.copyWith(notice: message));
        } else {
          _emit(_state.copyWith(error: message));
          if (fatal) unawaited(stop());
        }
      case _:
        break;
    }
  }

  void _applyTranscript({
    required String role,
    required String text,
    required bool isFinal,
    String? lang,
  }) {
    if (text.isEmpty) return;
    final turns = List<TranslateTurn>.of(_state.turns);

    if (role == 'user') {
      // A finalised turn is closed; anything new starts the next one.
      final openIndex = turns.isNotEmpty && !turns.last.heardFinal
          ? turns.length - 1
          : -1;
      // The model stays silent on speech already in the target language, so
      // that verdict can be reached the moment the language is known — no
      // waiting to see whether a translation shows up.
      final sameLanguage =
          lang != null && lang == _state.targetLanguage;
      final turn = TranslateTurn(
        heard: text,
        heardLang: lang,
        heardFinal: isFinal,
        sameLanguage: sameLanguage,
        translated: openIndex >= 0 ? turns[openIndex].translated : '',
      );
      if (openIndex >= 0) {
        turns[openIndex] = turn;
      } else {
        turns.add(turn);
      }
    } else {
      // The translation belongs to the most recent heard turn.
      if (turns.isEmpty) return;
      turns[turns.length - 1] =
          turns.last.copyWith(translated: text, sameLanguage: false);
    }

    if (turns.length > maxTurns) {
      turns.removeRange(0, turns.length - maxTurns);
    }
    _emit(_state.copyWith(turns: turns));
  }

  void _onFrame(DecodedFrame frame) {
    if (frame.tag != FrameTag.outputAudio) return;
    if (_state.captionsOnly) return;
    unawaited(_player.feed(frame.payload));
  }

  Future<void> dispose() async {
    _disposed = true;
    _glassesGrace?.cancel();
    _glassesGrace = null;
    await _glassesSub?.cancel();
    _glassesSub = null;
    await _stopAudio();
    await _teardownSocket();
    await _voiceAudioMode.exit();
    unawaited(Notifications.clearActivity());
    await _stateController.close();
  }

  /// A standing notification while translating.
  ///
  /// Translation is something you start and then put the phone down for, so it
  /// has to be visible from outside the app — both so the user can get back to
  /// it, and so a session quietly holding the microphone is never invisible.
  Future<void> _showNotification() async {
    try {
      await Notifications.showActivity(
        'Live translation',
        'Listening — translating into '
            '${translateLanguageName(_state.targetLanguage)}',
      );
    } catch (e) {
      _log.warn('translate notification failed: $e');
    }
  }
}
