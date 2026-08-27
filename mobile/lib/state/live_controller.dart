import 'dart:async';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart'
    show Clipboard, ClipboardData, ServicesBinding;
import 'package:flutter_contacts/flutter_contacts.dart';
import 'package:url_launcher/url_launcher.dart';

import '../capture/capture_source.dart';
import '../capture/device_registry.dart';
import '../capture/glasses_capture_source.dart';
import '../capture/mic_gate.dart';
import '../capture/phone_capture_source.dart';
import '../core/cache_patch.dart';
import '../core/chat_history.dart';
import '../core/config.dart';
import '../core/location.dart';
import '../core/log_store.dart';
import '../core/logger.dart';
import '../core/media_saver.dart';
import '../core/notifications.dart';
import '../data/data_api.dart';
import '../data/finder_api.dart';
import '../data/live_client.dart';
import '../features/glasses_lab/bridge/glasses_channel.dart';
import '../playback/pcm_player.dart';
import '../playback/voice_audio_mode.dart';
import '../protocol/frames.dart';
import '../protocol/messages.dart';
import '../protocol/protocol.dart';
import 'live_state.dart';
import 'permissions.dart';

/// Verdict for a final user transcript from the global transcript guard.
enum _UserVerdict { accept, replace, reject }

/// Orchestrates the whole realtime session: it wires the active
/// [CaptureSource] → [WebSocketLiveClient] → [PcmPlayer] and projects everything
/// into an observable [LiveSessionState].
///
/// Responsibilities:
///   * On connect, the client performs the `hello`+`config` handshake.
///   * Pipe [CaptureSource.audio16k] → INPUT_AUDIO (0x01) frames and
///     [CaptureSource.jpegFrames] → INPUT_VIDEO (0x02) frames.
///   * Feed OUTPUT_AUDIO (0x03) frames into the [PcmPlayer].
///   * Mic toggle sends `audio_start`/`audio_stop`; tapping mic while the
///     assistant is speaking triggers barge-in (`interrupt` + `flush()`).
///   * Translate server events into transcripts, tool activity, and state.
///
/// This type is framework-agnostic (no Riverpod import) so it is easy to test;
/// `providers.dart` adapts it into a Riverpod `Notifier`.
class LiveController {
  LiveController({
    required AppConfig config,
    required DeviceRegistry registry,
    required PcmPlayer player,
    required PermissionsService permissions,
    required WebSocketLiveClient Function(AppConfig, DeviceInfo Function())
        clientFactory,
    String? platform,
    GlassesBridgeApi? glassesBridge,
    int? Function()? currentUserId,
  })  : _currentUserId = currentUserId ?? (() => null),
        _config = config,
        _registry = registry,
        _player = player,
        _permissions = permissions,
        _glassesBridge = glassesBridge,
        platform = platform ?? defaultPlatform {
    _client = clientFactory(_config, _activeDeviceInfo);
    _bindClient();
    // Wi-Fi flap recovery: the moment the OS reports a usable network again,
    // poke the client so a pending backoff wait ends NOW. Without this a
    // flap costs the backoff delay on top of the outage itself — seconds of
    // "Listening…" with nobody listening (log-proven 2026-08-26).
    //
    // Skipped when no Flutter binding exists (every LiveController unit
    // test): the plugin's EventChannel throws from INSIDE its stream's
    // onListen, which escapes to the uncaught-zone handler where no
    // try/catch around .listen can reach it. The watch is an optimization —
    // its absence must never take the controller down. Errors after a
    // successful subscribe (plugin missing on some platform) land in
    // onError and are just logged.
    if (_bindingReady()) {
      _connectivitySub = Connectivity().onConnectivityChanged.listen(
        (results) {
          if (results.any((r) => r != ConnectivityResult.none)) {
            _client.nudge();
          }
        },
        onError: (Object e) =>
            _log.debug('connectivity watch unavailable: $e'),
      );
    }
    // Push the glasses storage-retention policy to native up front. This only
    // sets a field on the (singleton) native SDK — it does NOT need the glasses
    // to be connected — so doing it here guarantees the policy is in place
    // before ANY sync/download completes, regardless of how or where the
    // glasses later connect (live session, Glasses Lab, or an auto-reconnect
    // whose repeat 'connected' event is de-duplicated and never forwarded).
    unawaited(
      _glassesBridge?.setRetentionDays(_config.glassesRetentionDays) ??
          Future<void>.value(),
    );
    // Same reasoning for the recording length, and the same de-duplicated
    // 'connected' event makes it necessary: the glasses can be linked before
    // this controller ever subscribes (device-seen 2026-08-08 — an auto-connect
    // completed first and the connect-time push never ran). Native persists it
    // and re-applies it on a warm link, so seeding it here is what guarantees
    // the firmware stops at the length the user picked.
    unawaited(
      _glassesBridge?.setVideoDuration(_config.videoRecordSeconds) ??
          Future<void>.value(),
    );
  }

  static final _log = Logger('LiveController');

  /// Default platform string derived from the host OS.
  static String get defaultPlatform =>
      defaultTargetPlatform == TargetPlatform.iOS ? 'ios' : 'android';

  final DeviceRegistry _registry;
  final PcmPlayer _player;
  final PermissionsService _permissions;
  final String platform;

  AppConfig _config;
  late final WebSocketLiveClient _client;

  /// OS connectivity events → [WebSocketLiveClient.nudge] (see constructor).
  StreamSubscription<List<ConnectivityResult>>? _connectivitySub;

  /// Whether a Flutter binding exists — false in plain unit tests, where
  /// platform channels (and so the connectivity watch) cannot work.
  static bool _bindingReady() {
    try {
      ServicesBinding.instance;
      return true;
    } catch (_) {
      return false;
    }
  }

  // Capture stream plumbing for the *currently active* source.
  StreamSubscription<Uint8List>? _audioSub;
  StreamSubscription<Uint8List>? _videoSub;
  StreamSubscription<GlassesStatus>? _glassesSub;

  /// Optional glasses bridge for wear-to-talk (null in unit tests). Wear-on
  /// auto-opens the mic, wear-off pauses it — no long-press, hands-free.
  final GlassesBridgeApi? _glassesBridge;

  /// Who the cached notes/tasks belong to. A callback, not a stored id: this
  /// controller outlives a sign-in, and a stale id would file one user's notes
  /// under another's cache key — the bug the backend was fixed for on
  /// 2026-07-15, re-created on the client where no server scoping can catch it.
  final int? Function() _currentUserId;
  StreamSubscription<GlassesLabEvent>? _wearSub;
  bool _glassesWorn = false;

  // Auto-sync glasses media (WiFi-P2P) so photos the user snaps on the glasses
  // reach the phone gallery — and clear off the glasses per the retention
  // policy — WITHOUT ever tapping "Start sync". Triggered on connect and,
  // debounced, after each photo is stored. Deferred while the assistant is
  // speaking so the WiFi-P2P bring-up can't hiccup mid-turn.
  Timer? _autoSyncTimer;
  Timer? _autoSyncWatchdog;
  bool _autoSyncing = false;

  // Server stream plumbing.
  StreamSubscription<ServerMessage>? _eventSub;
  StreamSubscription<DecodedFrame>? _frameSub;
  StreamSubscription<ConnectionStatus>? _statusSub;

  // Echo guard: the mic stays muted while the assistant's TTS is actually
  // *playing* — not just while `speaking` state is set. The player keeps
  // draining buffered audio after the server's audio_end, so we mute until that
  // audio has finished (its byte-count tells us its duration) plus a margin.
  /// Holds back non-speech audio so the provider never scores room noise as a
  /// turn (and the transcriber never invents words for it).
  /// Voice-call audio path while a session runs (speakerphone only — the
  /// native side skips it for glasses/earbuds).
  final VoiceAudioMode _voiceAudioMode = VoiceAudioMode();

  late final MicGate _micGate = MicGate()
    ..onOpen = (rms, threshold) {
      // The moment speech energy crossed the bar IS when Farry began hearing
      // this utterance. The transcript arrives from the model only after the
      // user stops speaking (input transcription lands as a lump), so a
      // bubble stamped at transcript-arrival read as "Farry heard me late"
      // when the audio had been streaming live all along (user-reported
      // 2026-08-27). Remember the true start; the user bubble consumes it.
      _utteranceStartAt ??= DateTime.now();
      _log.info(
          'mic gate opened (level ${rms.round()} > bar ${threshold.round()})');
    };

  /// When the current utterance's speech energy first opened the mic gate —
  /// the honest "Farry started hearing" moment for the next user bubble.
  DateTime? _utteranceStartAt;

  bool _ttsActive = false;

  // Turn-latency instrumentation (client side, mirrors the backend's
  // turn.timing log): when Farry first HEARD this utterance, and when the
  // user's words last grew — so 'AI started speaking' can log the silence
  // the user actually sat through, as measured on the phone itself.
  DateTime? _turnHeardAt;
  DateTime? _turnLastUserWordsAt;

  int _ttsBytes = 0; // OUTPUT_AUDIO bytes fed since the turn's audio started
  DateTime? _ttsStart;
  Timer? _ttsClear;

  // 24 kHz mono PCM16 → 48000 bytes per second of playback.
  static const int _ttsBytesPerSec = 48000;

  // How long to keep the mic muted *after* the TTS audio should have finished
  // playing. Covers the OS audio buffer drain, the speaker's physical decay,
  // and room reverb — without this tail margin the mic re-opens while the last
  // word is still audible and the assistant's own voice echoes back in as a
  // bogus "user" turn (the garbled chat the user saw).
  //
  // History: 1200 ms originally (no echo, but felt slow), then 450 ms (faster,
  // but on a loud device the assistant's own voice tail leaked back in as a
  // bogus "user" turn and it answered itself — seen in real logs). 800 ms is
  // the balance: it covers the speaker decay + room ring-down so the echo is
  // gone, while staying far snappier than the old 1.2 s.
  static const int _ttsTailMarginMs = 800;

  /// [_ttsTailMarginMs] as a Duration, for the player's drain check.
  static const Duration _ttsTail = Duration(milliseconds: _ttsTailMarginMs);

  // ---- Observable state --------------------------------------------------

  final _stateController =
      StreamController<LiveSessionState>.broadcast(sync: false);

  /// Stream of state snapshots for the UI.
  Stream<LiveSessionState> get stateStream => _stateController.stream;

  // Latest camera JPEG frame, kept so the live-scan button and (via the cached
  // server-side frame) the identify_image tool can inspect the current view.
  Uint8List? _lastFrame;

  /// Set while a deliberately-requested single frame is in flight.
  ///
  /// The frame listener drops frames while the assistant is speaking, which is
  /// right for a continuous stream and wrong for a picture something is
  /// waiting on. This marks the difference for the phone; the glasses are
  /// one-shot by nature and never needed it.
  bool _oneShotPending = false;

  /// The most recent camera frame (raw JPEG), or null if the camera is off.
  Uint8List? get lastFrame => _lastFrame;

  /// The freshest camera frame, turning the camera on if it is off and waiting
  /// up to ~2s for the first one to arrive.
  ///
  /// The camera no longer opens with the session, so "off" is the normal state
  /// rather than something the user did. Returning null for it would have made
  /// the scan button and the `identify_image` tool answer "no camera frame" on
  /// a phone whose camera works perfectly — so this starts it instead. The
  /// wait was already here for the case where the camera had just resumed;
  /// starting from cold takes the same path.
  ///
  /// Leaves it on afterwards, deliberately: somebody who asked to be seen once
  /// is usually about to ask again, and the toggle is right there when they
  /// are done.
  Future<Uint8List?> grabFrame() async {
    // A cached frame counts ONLY while the camera is streaming, where it is at
    // most a second old and is genuinely what the camera sees.
    //
    // With the camera off it is stale by definition — the camera was closed
    // after it was taken — and returning it was quietly ruining every question
    // after the first. `grabFrame` handed back the old bytes, took no new
    // picture and sent nothing, so the tool waiting at the other end sat out
    // its full eight seconds and the model answered blind. On the phone this
    // read as "it captured, and the answer was wrong": the first question
    // worked, every one after it was guesswork (device-seen 2026-08-16 —
    // tool.ok at 8002, 8003, 8004 ms, one lone frame_forwarded between them).
    if (_state.cameraOn && _lastFrame != null) return _lastFrame;
    if (!_state.cameraOn) {
      // ONE frame, then the camera closes again. It used to switch the camera
      // on and leave it on, so a single tap on Scan bought a frame a second
      // for the rest of the conversation — thousands of pictures of whatever
      // the phone was pointing at, none of them asked for.
      //
      // The camera button is the other way in and still streams: someone who
      // turns it on has asked to be watched, and can see the preview and turn
      // it off. What must never happen is frames going out because something
      // else needed one picture.
      //
      // The listener has to exist BEFORE the shutter, or the frame is emitted
      // into a stream nobody is reading and the answer comes back blind.
      await _attachFrameListener();
      // Mark it as asked-for, so the "drop frames while speaking" rule lets it
      // through — the model is usually talking at exactly this moment.
      _oneShotPending = true;
      // Drop the old one first, so the wait below is watching for THIS
      // picture and cannot be satisfied by the last one.
      _lastFrame = null;
      try {
        await _videoSource.captureOnce();
      } catch (_) {
        _oneShotPending = false;
        rethrow;
      }
    }
    for (var i = 0; i < 8 && _lastFrame == null; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 250));
    }
    return _lastFrame;
  }

  // Voice (`identify_image`) results, surfaced so the UI can present the same
  // result sheet the scan button shows.
  final _finderController = StreamController<FinderDetection>.broadcast();

  /// Detections produced by the `identify_image` voice tool.
  Stream<FinderDetection> get finderEvents => _finderController.stream;

  LiveSessionState _state = const LiveSessionState();
  LiveSessionState get state => _state;

  void _emit(LiveSessionState next) {
    _state = next;
    if (!_stateController.isClosed) _stateController.add(next);
  }

  CaptureSource get _audioSource => _registry.audioSource;
  CaptureSource get _videoSource => _registry.videoSource;

  /// Wear-to-talk (B1-C): just watch for wear events — put-on opens the mic
  /// hands-free, take-off pauses. We do NOT auto-connect the glasses on every
  /// session (that caused 20 s connect-timeout churn when the glasses were
  /// off, and hands-free doesn't need them). The user connects glasses
  /// explicitly (Glasses Lab / glasses mic); wear then drives the mic if the
  /// firmware ever reports it. No-op without a glasses bridge (unit tests).
  Future<void> _startWearToTalk() async {
    final bridge = _glassesBridge;
    if (bridge == null) return;
    try {
      _wearSub ??= bridge.events().listen(_onGlassesEvent);
    } catch (e) {
      _log.warn('wear-to-talk setup failed: $e');
    }
  }

  /// Voice tool `connect_glasses`: connect the saved glasses (asked-and-
  /// confirmed by the model before this fires). Status lands in the banner
  /// via the wear/connection watcher.
  Future<void> _connectSavedGlasses() async {
    final bridge = _glassesBridge;
    if (bridge == null) return;
    // Robustness: ignore a duplicate connect while one is already up/in flight
    // (the model sometimes calls the tool twice).
    if (_state.glassesConnected || _connectingGlasses) return;
    _connectingGlasses = true;
    try {
      final info = await bridge.bridgeInfo();
      final savedMac = info['lastMac'] as String?;
      if (savedMac != null && savedMac.isNotEmpty) {
        // FAST PATH: connect the saved device directly — no scan. A mid-session
        // BLE scan holds the radio for seconds and stalls the audio we're
        // streaming to Gemini (seen as a 1011 "deadline expired" session drop),
        // so we avoid it in the common case. If the saved unit is actually off
        // (two-glasses case), the watchdog below falls back to a scan.
        _log.info('connect_glasses → $savedMac (direct)');
        await bridge.connect(savedMac);
        _scheduleGlassesScanFallback(bridge, savedMac);
      } else {
        // No saved device — we have no choice but to scan (also surfaces units
        // paired in Android BT settings) and connect the best candidate.
        await _scanAndConnectBest(bridge, null);
      }
    } catch (e) {
      _log.warn('connect_glasses failed: $e');
    } finally {
      // Backstop: always clear the in-flight guard after the connect watchdog
      // window. (It's also cleared the instant a connectionState event lands —
      // see _onGlassesEvent.) Previously this only cleared when NOT connected,
      // so a successful connect left the guard stuck true forever and every
      // later reconnect — e.g. starting a second session without killing the
      // app — was silently blocked.
      Future<void>.delayed(const Duration(seconds: 24), () {
        _connectingGlasses = false;
      });
    }
  }

  /// If the direct connect to [triedMac] hasn't landed within the connect
  /// watchdog window, the saved unit is probably off — scan and connect
  /// whichever glasses is actually present now.
  void _scheduleGlassesScanFallback(GlassesBridgeApi bridge, String triedMac) {
    Future<void>.delayed(const Duration(seconds: 12), () async {
      if (_state.glassesConnected) return; // direct connect worked
      _log.info('connect_glasses: $triedMac did not connect — scan fallback');
      // The native connect-serialization guard holds isConnecting for the
      // whole ~60 s watchdog window and SKIPS scans while it does — so this
      // fallback silently never scanned (broken since the guard landed
      // 2026-07-11). Disconnect first to free the guard; connect() re-arms
      // the auto-reconnect latch afterwards.
      try {
        await bridge.disconnect();
      } catch (e) {
        _log.warn('scan fallback disconnect failed: $e');
      }
      final attempted = await _scanAndConnectBest(bridge, triedMac);
      if (!attempted) {
        // Nothing found: re-issue the saved-MAC connect so the vendor SDK's
        // own reconnect (setReConnectMac + setNeedConnect) stays armed —
        // the disconnect above would otherwise leave auto-reconnect latched
        // off until the next manual connect.
        try {
          await bridge.connect(triedMac);
        } catch (e) {
          _log.warn('scan fallback re-arm failed: $e');
        }
      }
    });
  }

  /// Scan and connect the best-available glasses: powered-on-now wins, then a
  /// live BLE advertiser, then anything we saw. [skipMac] is the unit we just
  /// failed to reach (avoid retrying the dead one first). Returns whether a
  /// connect was actually issued (false = nothing found).
  Future<bool> _scanAndConnectBest(
      GlassesBridgeApi bridge, String? skipMac) async {
    final hits = await bridge.scan(timeout: const Duration(seconds: 6));
    if (hits.isEmpty) {
      _log.warn('connect_glasses: no glasses found — turn them on');
      return false;
    }
    final live = hits.where((h) => h.connected && h.mac != skipMac).toList();
    final advertising =
        hits.where((h) => h.rssi != 0 && h.mac != skipMac).toList();
    final mac = live.isNotEmpty
        ? live.first.mac
        : advertising.isNotEmpty
            ? advertising.first.mac
            : hits.first.mac;
    _log.info('connect_glasses → $mac (from scan)');
    await bridge.connect(mac);
    return true;
  }

  /// Glasses-card connect flow, step 1: clean slate + scan. The disconnect
  /// first aborts any wedged attempt to a dead unit so the scan isn't skipped
  /// by the native connect-serialization guard (the saved-MAC fast path would
  /// otherwise hold it for the full ~60 s watchdog window). Returns everything
  /// found, best-first (classic-BT-connected unit, then live advertisers), so
  /// the card can offer a chooser when more than one pair is out there.
  Future<List<GlassesDeviceHit>> scanGlassesForPicker() async {
    final bridge = _glassesBridge;
    if (bridge == null || _connectingGlasses) return const [];
    try {
      await bridge.disconnect();
      final hits = await bridge.scan(timeout: const Duration(seconds: 6));
      // rssi == 0 means "never heard" (bonded fold-in), not "excellent
      // signal" — rank those LAST or the sheet lists a powered-off unit
      // above the live one (device-seen 2026-08-03).
      int eff(GlassesDeviceHit h) => h.rssi == 0 ? -1000 : h.rssi;
      final sorted = [...hits]..sort((a, b) {
          if (a.connected != b.connected) return a.connected ? -1 : 1;
          return eff(b).compareTo(eff(a));
        });
      return sorted;
    } catch (e) {
      _log.warn('glasses scan (card) failed: $e');
      return const [];
    }
  }

  /// Glasses-card connect flow, step 2: connect the unit the user picked (or
  /// the only one found). Whoever connects becomes the persisted auto-connect
  /// target (last_mac + setReConnectMac, native side).
  Future<void> connectGlassesTo(String mac) async {
    final bridge = _glassesBridge;
    if (bridge == null) return;
    if (_state.glassesConnected || _connectingGlasses) return;
    _connectingGlasses = true;
    try {
      await bridge.connect(mac);
    } catch (e) {
      _log.warn('connectGlassesTo failed: $e');
    } finally {
      Future<void>.delayed(const Duration(seconds: 24), () {
        _connectingGlasses = false;
      });
    }
  }

  /// Settings volume slider → the glasses' own speaker (music/A2DP) volume.
  /// The native side persists the level and re-applies it on every connect,
  /// so this works even while disconnected (takes effect next connect).
  Future<void> setGlassesVolume(int level) async {
    try {
      await _glassesBridge?.setVolume('music', level.clamp(0, 100));
    } catch (e) {
      _log.warn('setGlassesVolume failed: $e');
    }
  }

  // ---- Glasses video recording -------------------------------------------
  //
  // Self-contained: the only state it owns is [_state.recording] plus one
  // ticker, and every path clears both. Nothing else in the session reads it,
  // so a fault here cannot reach the mic, the socket or a capture.

  /// Repaints the on-screen clock. The elapsed time itself comes from
  /// [GlassesRecording.startedAt], so a missed tick is cosmetic.
  Timer? _recordingTicker;

  /// Whether the mic was open before the recording, so it can be put back the
  /// way the user had it — and only that way.
  bool _micWasOpenBeforeRecording = false;

  /// A start already in progress. Silencing Farry takes a moment (the speaker
  /// has to drain), and a second tap during that window must not queue up a
  /// second recording.
  bool _startingRecording = false;

  /// Longest we wait for the speaker to fall silent before recording anyway.
  /// A stuck playback must not block the feature — but it must not be able to
  /// bleed a whole sentence into the video either.
  static const _recordingSilenceTimeout = Duration(seconds: 3);

  /// Longest a spoken "starting the recording" may take before we roll without
  /// it. Generous: cutting the confirmation off is worse than a short wait,
  /// and the user asked for this out loud so they are expecting an answer.
  static const _recordingReplyTimeout = Duration(seconds: 8);

  /// How long to let that reply START arriving. A voice tool call reaches us
  /// BEFORE its audio does, so without this "nothing is playing" would read as
  /// "nothing to wait for" and the confirmation would be swallowed.
  static const _recordingReplyGrace = Duration(milliseconds: 1500);

  /// Where a `record_video` request lands: glasses when they're connected,
  /// otherwise the PHONE camera. The glasses were mandatory here once, and a
  /// bare "glasses aren't connected" refusal on a phone with a perfectly good
  /// camera was the wrong answer (user-called-out 2026-08-27).
  Future<void> startRecording({bool afterSpokenReply = false}) {
    if (_state.glassesConnected && _glassesBridge != null) {
      return startGlassesRecording(afterSpokenReply: afterSpokenReply);
    }
    return _startPhoneRecording(afterSpokenReply: afterSpokenReply);
  }

  /// Stop whichever recording is running (glasses or phone).
  Future<void> stopRecording() {
    final src = _videoSource;
    if (src is PhoneCaptureSource && src.isRecordingVideo) {
      return _stopPhoneRecording();
    }
    return stopGlassesRecording();
  }

  /// Record with the phone camera for the configured duration. Mirrors the
  /// glasses flow's silence dance, with one addition: the assistant's OWN mic
  /// capture is stopped first — the camera's recorder needs the microphone,
  /// and two owners is how a video ends up silent.
  Future<void> _startPhoneRecording({bool afterSpokenReply = false}) async {
    final src = _videoSource;
    if (src is! PhoneCaptureSource) {
      _reportRecordingFailure('not_connected', spoken: afterSpokenReply);
      return;
    }
    if (_state.recording != null || _startingRecording) return;
    _startingRecording = true;
    _emit(_state.copyWith(recordingBusy: true));
    try {
      _micWasOpenBeforeRecording = _state.micOpen;
      _recordingAskedByVoice = afterSpokenReply;
      if (afterSpokenReply) {
        await _waitForSilence(_recordingReplyTimeout,
            grace: _recordingReplyGrace);
      } else if (_state.liveState == LiveState.speaking || _ttsActive) {
        await interrupt();
      }
      if (_state.micOpen) await stopListening(); // frees the microphone
      await _waitForSilence(_recordingSilenceTimeout);
      _cameraWasOnBeforeRecording = _state.cameraOn;
      await src.startVideoRecording();
      final seconds = _config.videoRecordSeconds;
      _emit(_state.copyWith(
        recording: GlassesRecording(
          requestId: 'phone',
          seconds: seconds,
          startedAt: DateTime.now(),
        ),
        // Show the recorder's live preview: the user must SEE what the phone
        // is recording (cameraController now points at the recorder).
        cameraOn: true,
      ));
      _recordingTicker?.cancel();
      _recordingTicker = Timer.periodic(const Duration(seconds: 1), (_) {
        _emit(_state.copyWith());
        final rec = _state.recording;
        // The firmware auto-stops a glasses recording; the phone's stops HERE.
        if (rec != null && rec.elapsed.inSeconds >= rec.seconds) {
          unawaited(_stopPhoneRecording());
        }
      });
      unawaited(Notifications.showActivity(
        'Recording video',
        '0:00 / ${GlassesRecording.format(Duration(seconds: seconds))}',
        progress: 0,
      ));
    } catch (e) {
      _log.warn('phone recording failed to start: $e');
      _emit(_state.copyWith(lastError: "Couldn't start the recording."));
      _reportRecordingFailure('camera_off', spoken: afterSpokenReply);
      _restoreAfterRecording();
    } finally {
      _startingRecording = false;
      _emit(_state.copyWith(recordingBusy: false));
    }
  }

  /// Whether the streaming camera was on before a phone recording, so the
  /// preview can be handed back exactly as the user had it.
  bool _cameraWasOnBeforeRecording = false;

  /// Stop the phone recording, move the file into the gallery, tell the user.
  Future<void> _stopPhoneRecording() async {
    final src = _videoSource;
    if (src is! PhoneCaptureSource) return;
    final finished = _state.recording;
    _recordingTicker?.cancel();
    _recordingTicker = null;
    final path = await src.stopVideoRecording();
    if (_state.recordingBusy) _emit(_state.copyWith(recordingBusy: false));
    if (_state.recording != null) _emit(_state.copyWith(clearRecording: true));
    // The recorder (and its preview) is gone; hand the camera state back the
    // way the user had it — streaming preview restored only if it was on.
    _emit(_state.copyWith(cameraOn: false));
    if (_cameraWasOnBeforeRecording) {
      _cameraWasOnBeforeRecording = false;
      unawaited(_startVideo());
    }
    String noticeText;
    if (path != null) {
      final uri = await MediaSaver.saveVideoFromPath(path);
      final length = finished == null
          ? ''
          : ' (${GlassesRecording.format(finished.clampedElapsed)})';
      noticeText = uri != null
          ? 'Recording finished$length — saved to your gallery (Movies/Farry).'
          : 'Recording finished$length — but saving it to the gallery failed.';
    } else {
      noticeText = 'The recording could not be saved.';
    }
    unawaited(Notifications.showActivity('Recording finished', noticeText,
        done: true));
    _emit(_state.copyWith(
      transcripts: [
        ..._state.transcripts,
        TranscriptEntry(role: 'notice', text: noticeText, isFinal: true),
      ],
    ));
    _restoreAfterRecording();
    if (_state.connection == ConnectionStatus.connected) {
      _client.send(const TextMessage(
        '(System note: the video recording just finished and was saved to '
        'the gallery. Tell me so in ONE short sentence, then stop.)',
      ));
    }
  }

  /// Start recording on the glasses for the configured duration.
  ///
  /// Farry goes fully quiet FIRST. The glasses record their audio from their
  /// own microphone, so the order here is the feature: interrupt whatever is
  /// being said, close the mic, wait for the speaker to actually finish, and
  /// only then tell the glasses to roll. Starting first and silencing after
  /// would put the tail of a sentence into every video.
  /// [afterSpokenReply] is set when the request came by voice: Farry is about
  /// to say she's starting, and cutting that off would leave someone wearing
  /// the glasses with no idea whether it worked. So that path waits for the
  /// confirmation to finish instead of interrupting it.
  Future<void> startGlassesRecording({bool afterSpokenReply = false}) async {
    final bridge = _glassesBridge;
    if (bridge == null) return;
    if (_state.recording != null || _startingRecording) return;
    if (!_state.glassesConnected) {
      _emit(_state.copyWith(
          lastError: 'Connect the glasses first to record video.'));
      // Tell the backend too, or the model — which asked for this — happily
      // announces "Recording started" over a recording that never began
      // (device-seen 2026-08-08). A banner the user isn't looking at is not
      // enough when the answer is spoken.
      _reportRecordingFailure('not_connected', spoken: afterSpokenReply);
      return;
    }
    _startingRecording = true;
    _emit(_state.copyWith(recordingBusy: true));
    try {
      _micWasOpenBeforeRecording = _state.micOpen;
      _recordingAskedByVoice = afterSpokenReply;
      // 1. Clear the speaker of anything Farry is saying.
      if (afterSpokenReply) {
        await _waitForSilence(_recordingReplyTimeout,
            grace: _recordingReplyGrace);
      } else if (_state.liveState == LiveState.speaking || _ttsActive) {
        await interrupt();
      }
      // 2. Close the mic — no more turns can start.
      if (_state.micOpen) await stopListening();
      // 3. Confirm the speaker really is empty.
      await _waitForSilence(_recordingSilenceTimeout);
      // 4. Only now: roll. The `recording` state comes from the videoState
      //    event, never from here — only the glasses can confirm they started.
      await bridge.startVideoRecording(_config.videoRecordSeconds);
    } catch (e) {
      _log.warn('startGlassesRecording failed: $e');
      _emit(_state.copyWith(lastError: "Couldn't start the recording."));
      _reportRecordingFailure('capture_failed', spoken: afterSpokenReply);
      _restoreAfterRecording();
    } finally {
      _startingRecording = false;
      _emit(_state.copyWith(recordingBusy: false));
    }
  }

  /// Poll until nothing is playing, capped so a wedged player can't hang the
  /// button forever. [grace] first gives audio that hasn't started yet a
  /// chance to appear, so a reply still in flight isn't mistaken for silence.
  Future<void> _waitForSilence(Duration cap,
      {Duration grace = Duration.zero}) async {
    bool speaking() => _ttsActive || _player.isPlayingWithin(Duration.zero);
    if (grace > Duration.zero) {
      final graceEnd = DateTime.now().add(grace);
      while (DateTime.now().isBefore(graceEnd) && !speaking()) {
        await Future<void>.delayed(const Duration(milliseconds: 100));
      }
    }
    final deadline = DateTime.now().add(cap);
    while (DateTime.now().isBefore(deadline)) {
      if (!speaking()) return;
      await Future<void>.delayed(const Duration(milliseconds: 100));
    }
    _log.warn('recording: speaker still busy after ${cap.inSeconds}s '
        '— starting anyway');
  }

  /// Give the session back exactly the mic state it had before recording.
  /// Playback needs no undoing: its gate is derived from [_state.recording],
  /// which the caller has already cleared.
  void _restoreAfterRecording() {
    if (_micWasOpenBeforeRecording && !_state.micOpen) {
      unawaited(startListening());
    }
    _micWasOpenBeforeRecording = false;
  }

  /// Stop early. The UI flips as soon as the glasses confirm.
  Future<void> stopGlassesRecording() async {
    final bridge = _glassesBridge;
    if (bridge == null || _state.recording == null) return;
    _emit(_state.copyWith(recordingBusy: true));
    try {
      await bridge.stopVideoRecording();
    } catch (e) {
      _log.warn('stopGlassesRecording failed: $e');
    }
    // Released by the videoState event that resolves the stop; this is the
    // backstop for a device that never answers at all.
    Timer(const Duration(seconds: 9), () {
      if (_state.recordingBusy) _emit(_state.copyWith(recordingBusy: false));
    });
  }

  /// Settings chooser → persist and push to the glasses.
  Future<void> setVideoRecordSeconds(int seconds) async {
    try {
      await _glassesBridge?.setVideoDuration(seconds);
    } catch (e) {
      _log.warn('setVideoDuration failed: $e');
    }
  }

  /// Whether the recording in flight was asked for by voice, so a failure
  /// arriving later (the glasses refusing, a timeout) still reaches the model
  /// that is waiting to speak about it.
  bool _recordingAskedByVoice = false;

  /// Report to the backend that a recording did not start. Reuses the
  /// `capture_failed` control message the vision tools already wait on, so
  /// there is one failure path for "the device could not do the thing",
  /// not two.
  void _reportRecordingFailure(String reason, {required bool spoken}) {
    if (!spoken) return; // nothing is waiting on it; the banner is enough
    _log.warn('recording failed ($reason) — reporting to the backend');
    _client.send(CaptureFailedMessage(reason: reason));
  }

  void _onVideoStateEvent(GlassesLabEvent event) {
    final state = event.data['state'] as String?;
    final requestId = (event.data['requestId'] as String?) ?? '';
    switch (state) {
      case 'recording':
        _recordingAskedByVoice = false;
        final seconds = (event.data['seconds'] as num?)?.toInt() ??
            _config.videoRecordSeconds;
        _recordingTicker?.cancel();
        // Repaint the on-screen clock every second, and refresh the
        // notification every 5 — the phone may be in a pocket, and a recording
        // the user cannot see running is one they cannot decide to stop.
        var ticks = 0;
        _recordingTicker = Timer.periodic(const Duration(seconds: 1), (_) {
          _emit(_state.copyWith());
          final rec = _state.recording;
          if (rec != null && ++ticks % 5 == 0) {
            unawaited(Notifications.showActivity(
              'Recording on your glasses',
              rec.label,
              progress: rec.seconds == 0
                  ? null
                  : (rec.clampedElapsed.inSeconds * 100 ~/ rec.seconds),
            ));
          }
        });
        unawaited(Notifications.showActivity(
          'Recording on your glasses',
          '0:00 / ${GlassesRecording.format(Duration(seconds: seconds))}',
          progress: 0,
        ));
        _emit(_state.copyWith(
          recording: GlassesRecording(
            requestId: requestId,
            seconds: seconds,
            startedAt: DateTime.now(),
          ),
        ));
      case 'stopped':
        final finished = _state.recording;
        _endRecording();
        _announceRecordingDone(finished);
        // Pull it onto the phone if the user asked for that. The debounce also
        // gives the glasses a moment to finalise the file.
        if (_config.autoMediaSync) {
          _scheduleAutoGlassesSync(const Duration(seconds: 6));
        } else {
          // Manual mode: don't transfer, but DO find out what is waiting, so
          // the dashboard can say so. One cheap BLE command, no WiFi.
          _refreshPendingMedia(const Duration(seconds: 6));
        }
      case 'failed':
        _endRecording();
        unawaited(Notifications.clearActivity());
        _reportRecordingFailure(
          (event.data['reason'] as String?) ?? 'capture_failed',
          spoken: _recordingAskedByVoice,
        );
        _recordingAskedByVoice = false;
        final detail = event.data['detail'] as String?;
        _emit(_state.copyWith(
          lastError: detail == null
              ? "The recording couldn't start."
              : 'Recording: $detail',
        ));
    }
  }

  /// End the recording locally and hand the session back to the user. Clearing
  /// `recording` is what lifts the playback gate, so it happens FIRST and
  /// unconditionally — every route out of a recording lands here.
  /// Tell the user the recording is done — out loud, on the dashboard, and in
  /// the notification shade. All three, because any one of them can be the
  /// only one they are looking at: the phone may be pocketed (voice), the
  /// screen may be open (dashboard), or they may come back later (shade).
  ///
  /// Safe to say out loud now: [_endRecording] has already cleared the
  /// recording, so the playback gate is open again.
  void _announceRecordingDone(GlassesRecording? finished) {
    final length = finished == null
        ? ''
        : ' (${GlassesRecording.format(Duration(seconds: finished.seconds))})';
    unawaited(Notifications.showActivity(
      'Recording finished',
      _config.autoMediaSync
          ? 'Saving it to your phone…'
          : "Waiting on your glasses — tap Sync when you're ready.",
      done: true,
    ));
    _emit(_state.copyWith(
      transcripts: [
        ..._state.transcripts,
        TranscriptEntry(
          role: 'notice',
          text: _config.autoMediaSync
              ? 'Recording finished$length — saving to your phone.'
              : 'Recording finished$length — waiting on your glasses.',
          isFinal: true,
        ),
      ],
    ));
    // And have Farry say it, the same way she announces a low battery.
    if (_state.connection == ConnectionStatus.connected) {
      _client.send(TextMessage(
        '(System note: the video recording on the glasses just finished'
        '$length. Tell me so in ONE short sentence, then stop.)',
      ));
    }
  }

  void _endRecording() {
    _recordingTicker?.cancel();
    _recordingTicker = null;
    if (_state.recordingBusy) _emit(_state.copyWith(recordingBusy: false));
    if (_state.recording != null) {
      _emit(_state.copyWith(clearRecording: true));
    }
    _restoreAfterRecording();
  }

  /// Settings "Glasses" card: user-initiated disconnect — the bridge marks it
  /// user-intended, so no auto-reconnect fights it.
  Future<void> disconnectGlasses() async {
    try {
      await _glassesBridge?.disconnect();
    } catch (e) {
      _log.warn('disconnect_glasses failed: $e');
    }
  }

  bool _connectingGlasses = false;

  /// Tracks the last glasses connection state so the camera auto-switches only
  /// on a real transition (connect → glasses cam, disconnect → phone cam).
  bool _glassesWasConnected = false;

  bool _lowBatteryWarned = false;

  /// Announce a low glasses battery once (via Farry), re-arming after it
  /// recovers. Visual red is handled by the banner.
  void _maybeWarnLowBattery(int pct) {
    if (pct > 25) _lowBatteryWarned = false;
    if (pct >= 20 || _lowBatteryWarned) return;
    if (_state.connection != ConnectionStatus.connected) return;
    _lowBatteryWarned = true;
    _log.info('glasses battery low ($pct%) — asking Farry to warn');
    _client.send(TextMessage(
      '(System note: the smart glasses battery is low at $pct%. Briefly warn '
      'me out loud in one short sentence, then continue.)',
    ));
  }

  void _onGlassesEvent(GlassesLabEvent event) {
    switch (event.type) {
      case 'connectionState':
        final connected = event.data['state'] == 'connected';
        _emit(_state.copyWith(
          glassesConnected: connected,
          glassesName: event.data['name'] as String?,
        ));
        // The connect attempt has resolved (either way) — release the in-flight
        // guard so a later reconnect (new session, or after a drop) can proceed.
        _connectingGlasses = false;
        // Audio route just changed under us. Glasses take the voice out of the
        // phone speaker, and communication mode would drag their Bluetooth
        // audio down to narrowband SCO — so hand the media path back while
        // they're connected, and reclaim the voice path when they drop.
        if (_state.connection == ConnectionStatus.connected) {
          unawaited(
            connected ? _voiceAudioMode.exit() : _voiceAudioMode.enter(),
          );
        }
        // Push the storage-retention policy to the freshly-connected glasses so
        // synced photos are pruned per the user's Settings choice.
        if (connected) {
          unawaited(
            _glassesBridge?.setRetentionDays(_config.glassesRetentionDays) ??
                Future<void>.value(),
          );
          // Keep the glasses' own recording length in step with the app's
          // setting. The FIRMWARE is what stops a recording, so a device that
          // never heard our choice would quietly use its own — and the user
          // would get a different length than the one they picked. Pushing it
          // here means it is already right before they ever hit record, even
          // if they have never opened the Video recording page.
          unawaited(
            _glassesBridge?.setVideoDuration(_config.videoRecordSeconds) ??
                Future<void>.value(),
          );
          // Pull anything already sitting on the glasses (photos taken while
          // disconnected) shortly after the link is up — or, in manual mode,
          // just find out what is there so the user can decide.
          if (_config.autoMediaSync) {
            _scheduleAutoGlassesSync(const Duration(seconds: 3));
          } else {
            _refreshPendingMedia(const Duration(seconds: 3));
          }
        }
        // Auto-pick the camera on a connect/disconnect TRANSITION: glasses
        // become the default camera the moment they connect, and it falls back
        // to the phone camera when they drop. (setVideoDevice no-ops if already
        // on that device, so this won't fight a matching manual choice.)
        if (connected != _glassesWasConnected) {
          _glassesWasConnected = connected;
          unawaited(setVideoDevice(connected
              ? CaptureDeviceKind.glasses
              : CaptureDeviceKind.phone));
        }
      case 'battery':
        final pct = (event.data['pct'] as num?)?.toInt();
        if (pct != null) {
          _emit(_state.copyWith(glassesBattery: pct));
          _maybeWarnLowBattery(pct);
        }
      case 'wearState':
        final worn = event.data['worn'] == true;
        _emit(_state.copyWith(glassesWorn: worn));
        if (worn == _glassesWorn) return;
        _glassesWorn = worn;
        _log.info('glasses ${worn ? "worn → listen" : "removed → pause"}');
        if (_state.connection != ConnectionStatus.connected) return;
        // Wear drives the mic only when it's the phone/earbuds (continuous);
        // the glasses' own mic is push-to-talk and can't auto-stream.
        if (worn) {
          if (!_state.micOpen) unawaited(startListening());
        } else {
          if (_state.micOpen) unawaited(stopListening());
        }
      case 'deviceEvent':
        // The firmware announces a freshly stored photo as a raw device event
        // (`photoStored count=N`). Debounce so a burst of shots batches into a
        // single sync instead of one WiFi bring-up per photo.
        final hex = event.data['hex'] as String?;
        if (hex != null && hex.startsWith('photoStored')) {
          if (_config.autoMediaSync) {
            _scheduleAutoGlassesSync(const Duration(seconds: 8));
          } else {
            _refreshPendingMedia(const Duration(seconds: 8));
          }
        }
      case 'syncedPhoto':
        // A photo that just came off the glasses — usually one the wearer took
        // with the glasses' own button, which has no BLE thumbnail and so has
        // been invisible until now. Shown, not sent: this is a picture landing
        // on the phone, not a question for Farry.
        final jpeg = event.data['jpeg'];
        if (jpeg is Uint8List && jpeg.isNotEmpty) {
          _emit(_state.copyWith(
            lastCapturedPhoto: jpeg,
            lastCapturedAt: DateTime.now(),
          ));
        }
      case 'mediaCount':
        _emit(_state.copyWith(
          pendingMedia: GlassesMedia(
            photos: (event.data['img'] as num?)?.toInt() ?? 0,
            videos: (event.data['vid'] as num?)?.toInt() ?? 0,
            recordings: (event.data['rec'] as num?)?.toInt() ?? 0,
          ),
        ));
      case 'videoState':
        _onVideoStateEvent(event);
      case 'syncProgress':
        // A sync run reaching 100% (files done / nothing to sync) frees the
        // in-flight guard so the next photo can trigger a fresh sync.
        final pct = (event.data['pct'] as num?)?.toInt() ?? 0;
        final file = (event.data['file'] as String?) ?? 'Syncing…';
        if (pct >= 100) {
          _autoSyncing = false;
          _autoSyncWatchdog?.cancel();
          _emit(_state.copyWith(clearSync: true));
          // A finished transfer replaces the ongoing line rather than adding
          // to it, and becomes dismissable.
          unawaited(Notifications.showActivity(
            file.contains('nothing') || file.contains('skipped')
                ? 'Nothing to sync'
                : 'Saved to your phone',
            file,
            done: true,
          ));
        } else {
          final sync = GlassesSync(
            file: file,
            pct: pct,
            speedKbps: (event.data['speedKbps'] as num?)?.toDouble(),
            index: (event.data['index'] as num?)?.toInt() ?? 0,
            total: (event.data['total'] as num?)?.toInt() ?? 0,
          );
          _emit(_state.copyWith(syncStatus: sync));
          // A 300 MB video takes minutes; without this the phone looks idle.
          // Name the file and say how many are queued — one percentage that
          // restarts at 0 for each clip reads like a stall.
          unawaited(Notifications.showActivity(
            sync.total > 1
                ? 'Saving ${sync.index} of ${sync.total} to your phone'
                : 'Saving to your phone',
            '$file · $pct%',
            progress: pct,
          ));
        }
    }
  }

  /// Ask the glasses what they are holding, after [delay]. Manual mode's
  /// substitute for a sync: it costs one BLE command and blocks nothing, so
  /// the "waiting to sync" chip stays truthful without taking the device.
  void _refreshPendingMedia(Duration delay) {
    final bridge = _glassesBridge;
    if (bridge == null) return;
    _pendingMediaTimer?.cancel();
    _pendingMediaTimer = Timer(delay, () {
      if (!_state.glassesConnected) return;
      unawaited(bridge.refreshMediaCounts().catchError((Object e) {
        _log.warn('refreshMediaCounts failed: $e');
      }));
    });
  }

  Timer? _pendingMediaTimer;

  /// Comfortably past the native side's own ceiling, which is now a hard
  /// whole-run deadline (`WIFI_SYNC_TOTAL_BUDGET_MS`, 240 s) rather than the
  /// old 60 s stall + 6 s settle + 60 s retry. That chain could be pushed
  /// forward indefinitely by a repeating control ack, which is what left a
  /// sync running — and the UI stuttering — for as long as the glasses stayed
  /// connected (device-seen 2026-08-26).
  ///
  /// This must OUTLAST the native deadline. A backstop that fires first clears
  /// the in-flight guard while a transfer is still going, and the next photo
  /// starts a second sync on top of it — two concurrent importAlbum calls
  /// wedge the P2P session, which is the state we are trying to get out of.
  @visibleForTesting
  static const autoSyncGuardTimeout = Duration(seconds: 270);

  /// Settings / dashboard "Sync now": transfer whatever is waiting, on demand.
  /// Reports refusals through [_state.lastError] rather than doing nothing.
  Future<void> syncGlassesNow() async {
    final bridge = _glassesBridge;
    if (bridge == null) return;
    if (!_state.glassesConnected) {
      _emit(_state.copyWith(
          lastError: "Connect the glasses to sync what's on them."));
      return;
    }
    if (_state.recording != null) {
      _emit(_state.copyWith(
          lastError: "Can't sync while the glasses are recording."));
      return;
    }
    if (_autoSyncing) return; // already running
    _autoSyncing = true;
    _autoSyncWatchdog?.cancel();
    _autoSyncWatchdog = Timer(autoSyncGuardTimeout, () {
      _autoSyncing = false;
    });
    try {
      await bridge.startWifiSync();
    } catch (e) {
      _log.warn('syncGlassesNow failed: $e');
      _autoSyncing = false;
      _emit(_state.copyWith(lastError: "Couldn't start the sync."));
    }
  }

  /// (Re)arm the debounced glasses media auto-sync. Collapses rapid triggers
  /// (photo bursts) into one run; the actual sync fires from [_runAutoGlassesSync].
  void _scheduleAutoGlassesSync(Duration delay) {
    if (_glassesBridge == null) return;
    _autoSyncTimer?.cancel();
    _autoSyncTimer = Timer(delay, _runAutoGlassesSync);
  }

  /// Kick a WiFi-P2P media sync if the glasses are connected and we're not
  /// already syncing. Defers while the assistant is speaking (TTS active) so a
  /// mid-turn WiFi-P2P bring-up can't drop the audio/backend link.
  void _runAutoGlassesSync() {
    final bridge = _glassesBridge;
    if (bridge == null || _autoSyncing) return;
    if (!_state.glassesConnected) return;
    if (_ttsActive) {
      _scheduleAutoGlassesSync(const Duration(seconds: 5)); // try again after
      return;
    }
    _autoSyncing = true;
    // Backstop: clear the guard even if a terminal syncProgress never lands
    // (sync stalls / glasses drop mid-run), so future photos still sync.
    // Must outlast the native worst case — a 60 s stall, a P2P reset, and a
    // 60 s retry — or this fires mid-recovery and lets a second transfer start
    // on top of the one being rescued.
    _autoSyncWatchdog?.cancel();
    _autoSyncWatchdog = Timer(autoSyncGuardTimeout, () {
      _autoSyncing = false;
    });
    unawaited(bridge.startWifiSync().catchError((Object e) {
      _log.warn('auto glasses sync failed: $e');
      _autoSyncing = false;
    }));
  }

  /// Mirror the glasses status into state when glasses back the mic, so the
  /// live screen can show a connected/battery/talking banner (B1-C).
  void _watchGlassesStatus() {
    _glassesSub?.cancel();
    _glassesSub = null;
    final src = _audioSource;
    if (src is GlassesCaptureSource) {
      _glassesSub = src.status.listen((s) {
        _emit(_state.copyWith(
          glassesConnected: s.connected,
          glassesBattery: s.battery,
          glassesTalking: s.talking,
          glassesAudioReady: s.audioReady,
          glassesAudioPaired: s.audioPaired,
        ));
      });
    } else {
      _emit(_state.copyWith(glassesConnected: false, glassesTalking: false));
    }
  }

  /// Composite `hello.device`: the mic comes from the audio source, the camera
  /// from the video source (B1-B: they can be different devices). We advertise
  /// the union of what each channel can actually produce.
  DeviceInfo _activeDeviceInfo() {
    final a = _registry.audioKind;
    final v = _registry.videoKind;
    final kind = a == v ? a.name : '${a.name}+${v.name}';
    return DeviceInfo(
      kind: kind,
      id: a == v ? _audioSource.info.id : '${_audioSource.info.id}/${_videoSource.info.id}',
      capabilities: [
        if (_audioSource.capabilities.audioIn) 'audio_in',
        if (_videoSource.capabilities.videoIn) 'video_in',
        'audio_out',
      ],
    );
  }

  // ---- Lifecycle ---------------------------------------------------------

  /// Acquire permissions, prepare the audio engine + capture device, and open
  /// the socket. Returns the permission outcome so the UI can show rationale.
  Future<PermissionOutcome> connect() async {
    final outcome = await _permissions.requestMicAndCamera();
    _emit(_state.copyWith(
      permissionsGranted: outcome == PermissionOutcome.granted,
      audioKind: _registry.audioKind.name,
      videoKind: _registry.videoKind.name,
      // A new session starts fresh: a cap reached in the previous one no longer
      // applies to this attempt (the user may have upgraded, or it's a new day).
      capReached: false,
    ));
    if (outcome != PermissionOutcome.granted) {
      _log.warn('permissions not granted: $outcome');
      return outcome;
    }

    // Voice-call audio path BEFORE the engines open, so playback and capture
    // are created on it (the platform echo canceller needs both on the same
    // path). No-op when audio is headed for glasses/earbuds — see
    // AudioModeChannel.
    await _voiceAudioMode.enter();

    await _player.initialize();
    await _audioSource.initialize();
    _watchGlassesStatus();
    // If the camera is a different device, initialize it too (same instance is
    // idempotent, so a double-init when both channels share a source is safe).
    if (!identical(_videoSource, _audioSource)) {
      await _videoSource.initialize();
    }
    // The camera stays OFF until somebody asks for it.
    //
    // It used to open with the session, so every conversation streamed a frame
    // a second whether or not anyone wanted to be seen — an hour of talking
    // sent 3,600 pictures of a ceiling to the model, and the camera is the
    // most expensive thing the phone can leave running. It is also the honest
    // default: an assistant that starts watching the moment it opens is not
    // what someone would choose if asked.
    //
    // It also takes the camera out of the path on start-up, which is where the
    // screen went black twice on a vivo V2246 (2026-08-15, see TEST_PLAN #12).
    // That fault is NOT understood yet, so this is not a fix for it — but a
    // camera that is not attached cannot be what breaks.
    //
    // Nothing is lost: [grabFrame] turns it on and waits for the first frame,
    // so the scan button and the `identify_image` tool still see the room.
    _foreground = true;

    _client.start();
    // Keep the mic legal + the CPU awake while the screen is off, so the user
    // can talk to Farry hands-free without the phone in hand (Android 11+ mutes
    // background mic capture unless a microphone foreground service is running).
    unawaited(Future(() async {
      try {
        await _glassesBridge?.startMicService();
      } catch (e) {
        _log.warn('startMicService failed: $e');
      }
    }));
    // Wear-to-talk: auto-connect the saved glasses in the background so wear
    // events flow, then let put-on / take-off drive the mic. Best with the
    // mic on phone/earbuds (the glasses mic is push-to-talk by hardware).
    unawaited(_startWearToTalk());
    // Hands-free: open the mic right away so the user can just talk. In
    // TAP-TO-TALK mode we leave it closed — the user taps the mic button to
    // speak, so a noisy room / TV / the assistant's own voice can't trigger a
    // phantom turn. Either way the mic button toggles it.
    if (_config.handsFree) {
      await startListening();
    } else {
      _log.info('tap-to-talk mode: mic stays closed until you tap it');
      _emit(_state.copyWith(liveState: LiveState.idle));
    }
    // Fetch the device location in the background and push it to the backend so
    // "where am I?" works. Non-blocking — GPS can take a few seconds and must
    // not delay the session.
    unawaited(_pushLocation());
    return outcome;
  }

  /// Refreshes the backend's location while a session is live, so "where am
  /// I?" stays answerable (and current) even hours in.
  Timer? _locationTimer;

  /// Resolve the current location and send it to the backend (best-effort).
  Future<void> _pushLocation() async {
    try {
      final fix = await LocationService.current();
      if (fix == null) return;
      // A fix that resolves BEFORE the socket finishes its handshake would be
      // silently dropped by send() — which left whole sessions with no
      // location at all when GPS answered from its warm cache faster than the
      // connect (seen on device 2026-08-27: Farry "can't see" the location).
      // The ready-handler re-pushes, so skipping here loses nothing.
      if (_state.connection != ConnectionStatus.connected) {
        _log.info('location fix ready before socket — deferred to ready');
        return;
      }
      _client.send(LocationUpdateMessage(fix.toJson()));
    } catch (e) {
      _log.warn('push location failed: $e');
    }
  }

  /// Push now and keep the fix fresh every 5 minutes while connected.
  /// Called from the ready handler, so reconnects re-arm it too.
  void _startLocationUpdates() {
    _locationTimer?.cancel();
    unawaited(_pushLocation());
    _locationTimer = Timer.periodic(
      const Duration(minutes: 5),
      (_) => unawaited(_pushLocation()),
    );
  }

  /// Tear down capture, playback, and the socket (keeps objects reusable).
  Future<void> disconnect() async {
    // Final save (autosaves already ran during the session), then let the
    // NEXT session open its own history entry.
    _historySaveTimer?.cancel();
    _locationTimer?.cancel();
    _locationTimer = null;
    unawaited(ChatHistoryStore.saveSession(_state.transcripts)
        .then((_) => ChatHistoryStore.beginSession()));
    // Hand the phone back its normal audio path (ringtone/media volume, normal
    // routing) — never leave it in call mode after a session.
    unawaited(_voiceAudioMode.exit());
    // Drop any resolved numbers held for this session (privacy hygiene).
    _contactNumbers.clear();
    await _stopAudio();
    await _stopVideo();
    await _player.stop();
    await _client.stop();
    // Session over — drop the mic foreground service + wake-lock.
    try {
      await _glassesBridge?.stopMicService();
    } catch (e) {
      _log.warn('stopMicService failed: $e');
    }
    _emit(_state.copyWith(
      micOpen: false,
      cameraOn: false,
      liveState: LiveState.idle,
    ));
  }

  // ---- Client event wiring ----------------------------------------------

  void _bindClient() {
    _statusSub = _client.status.listen((status) {
      _log.info('event: connection → ${status.name}');
      _emit(_state.copyWith(connection: status));
    });

    _frameSub = _client.frames.listen((frame) {
      if (frame.tag == FrameTag.outputAudio) {
        // Nothing may come out of the speakers while the glasses are
        // recording: they capture their audio from their OWN microphone, so
        // anything Farry says would be baked into the video.
        //
        // The gate is DERIVED from the recording state, not a flag of its own.
        // That is deliberate: the mic gate that once deafened the whole app
        // did so because a separate flag got stuck set. There is no second
        // flag here to get stuck — the moment `recording` clears, by any
        // route (stop, duration reached, timeout, disconnect), sound is back.
        if (_state.recording != null) return;
        _ttsBytes += frame.payload.length; // track how much TTS we must play out
        // Fire-and-forget; PcmPlayer applies its own backpressure.
        unawaited(_player.feed(frame.payload));
      }
    });

    _eventSub = _client.events.listen(_onServerMessage);
  }

  /// Mute the mic for the duration of an assistant turn's audio.
  void _beginTts() {
    _ttsClear?.cancel();
    _ttsActive = true;
    // Any gate-open captured up to here belonged to the turn being answered
    // (or to leaked speaker audio) — never to the NEXT user bubble.
    _utteranceStartAt = null;
    _ttsBytes = 0;
    _ttsStart = DateTime.now();
    // Safety: never stay muted forever if audio_end is somehow lost.
    _ttsClear = Timer(const Duration(seconds: 20), () => _ttsActive = false);
  }

  /// After audio_end, keep muted until the buffered audio has actually played
  /// (its byte count gives its duration) plus a ring-down margin.
  void _endTtsAfterPlayback() {
    final start = _ttsStart;
    final playMs = (_ttsBytes / _ttsBytesPerSec * 1000).round();
    final elapsedMs =
        start == null ? 0 : DateTime.now().difference(start).inMilliseconds;
    final remainingMs =
        (playMs - elapsedMs).clamp(0, 60000) + _ttsTailMarginMs;
    _ttsClear?.cancel();
    _ttsClear =
        Timer(Duration(milliseconds: remainingMs), () => _ttsActive = false);
  }

  void _onServerMessage(ServerMessage msg) {
    switch (msg) {
      case ReadyMessage():
        // Stamp the active AI on every subsequent log line so a shared debug
        // trail clearly shows which provider/model the user was talking to.
        LogStore.instance.setProvider(msg.model ?? _config.provider);
        _log.info('session ready (model: ${msg.model ?? "?"})');
        _emit(_state.copyWith(clearError: true));
        // A slow connect can land after the user already came back to the
        // foreground, leaving the camera released with nothing to restore it.
        // Now that we're connected, put it back if it should be on.
        unawaited(_ensureCameraMatchesIntent());
        // Reconnect restores session INTENT, not just the camera: if the mic
        // was open before the drop, the surviving audio subscription is still
        // pushing PCM but the server's VAD window belonged to the dead session
        // — re-open it so the user isn't silently unheard after a reconnect.
        // On a FIRST connect micOpen is still false (startListening runs
        // later), so this never double-sends audio_start. Also re-sync the
        // device kind so glasses/phone frame-wait budgets stay correct.
        if (_state.micOpen) {
          _client.send(const AudioStartMessage());
        }
        _notifyDeviceUpdate();
        // The socket is open NOW, so this push cannot be dropped — and each
        // reconnect lands here too, so the fresh backend session (which starts
        // with no cached location) is re-fed without waiting for the timer.
        _startLocationUpdates();
      case TranscriptMessage():
        _applyTranscript(msg);
      case AudioStartEvent():
        // Assistant begins speaking — mute the mic until playback drains.
        // The felt-latency number: how long the user waited between their
        // last heard words and the reply starting, measured on the phone
        // (so it includes the network legs the backend cannot see).
        final waited = _turnLastUserWordsAt == null
            ? null
            : DateTime.now().difference(_turnLastUserWordsAt!).inMilliseconds;
        _log.info(
            'event: AI started speaking'
            '${waited == null ? '' : ' (${waited}ms after user finished)'}');
        _turnHeardAt = null;
        _turnLastUserWordsAt = null;
        _beginTts();
        _emit(_state.copyWith(liveState: LiveState.speaking));
      case AudioEndEvent():
        // Server finished sending audio, but the player is still draining its
        // buffer — keep the mic muted for that remaining playback + a margin.
        _log.info('event: AI finished speaking');
        _endTtsAfterPlayback();
        if (_state.liveState == LiveState.speaking) {
          _emit(_state.copyWith(liveState: LiveState.idle));
        }
      case ToolCallMessage():
        _applyToolCall(msg);
      case ToolResultMessage():
        _applyToolResult(msg);
      case StateMessage():
        _log.info('event: state → ${msg.value.name}');
        _emit(_state.copyWith(liveState: msg.value));
      case ErrorMessage():
        _log.warn('server error ${msg.code}: ${msg.message}');
        if (msg.code == 'quota_exceeded') {
          // The session is ending because the daily cap is spent — not because
          // anything broke. Say so where the user is looking (a notice in the
          // transcript, the same amber line reminders use), and remember it so
          // the reconnect overlay can offer Upgrade instead of a bare Retry that
          // would just hit the cap again.
          _emit(_state.copyWith(
            capReached: true,
            transcripts: [
              ..._state.transcripts,
              TranscriptEntry(role: 'notice', text: msg.message, isFinal: true),
            ],
          ));
        } else {
          _emit(_state.copyWith(lastError: msg.message));
        }
      case PongMessage():
        break; // handled inside the client (heartbeat)
      case ResolveContactRequestMessage():
        _log.info('event: resolve contact "${msg.name}" (${msg.channel})');
        unawaited(_handleResolveContactRequest(msg));
      case OpenMessagingMessage():
        unawaited(_handleOpenMessaging(msg));
      case UnknownServerMessage():
        if (msg.type == 'session_expired') {
          // The server ended the session ON PURPOSE (idle timeout or the
          // max-session cap) and is about to close the socket. Without this
          // branch the close looked like a network drop, so the app silently
          // reconnected — and the idle clock started again with nobody
          // talking, churning sessions (and streaming billable mic audio)
          // for as long as the app stayed open. End cleanly instead and say
          // why. The conversation itself is NOT lost: the backend keeps a
          // resume handle per user, so the next session picks up the context.
          final reason = (msg.raw['reason'] as String?) ?? 'idle';
          _log.info('session ended by server (reason: $reason)');
          _emit(_state.copyWith(transcripts: [
            ..._state.transcripts,
            TranscriptEntry(
              role: 'notice',
              text: reason == 'idle'
                  ? 'Session paused — kuch der se koi baat nahi hui. '
                      'Start dabate hi wahin se continue hoga.'
                  : 'Session ki time limit poori ho gayi. '
                      'Start dabate hi wahin se continue hoga.',
              isFinal: true,
            ),
          ]));
          unawaited(disconnect());
        } else {
          _log.debug('unknown server message: ${msg.type}');
        }
    }
  }

  /// Cap on retained transcript lines. A long session would otherwise grow the
  /// list without bound, and since every streaming fragment copies the whole
  /// list, the per-fragment cost (and the UI work) would creep up quadratically
  /// — the "listing gets slow in a long conversation" symptom. Old lines scroll
  /// out of view anyway, so we keep only the most recent ones.
  static const int _maxTranscripts = 80;

  void _applyTranscript(TranscriptMessage msg) {
    // Echo suppression: while the assistant's TTS is playing (and its tail
    // margin) the mic is muted, so any "user" transcript in that window can
    // only be the assistant's own voice leaking back in. Drop it so it never
    // pollutes the chat or gets treated as a real turn.
    if (msg.role == 'user' && _ttsActive) return;

    // Turn clock: a user transcript delta is proof Farry is hearing the user
    // RIGHT NOW. First delta of an utterance logs "hearing you" (so silence
    // in the log means the words never reached the model — mic gate, mute,
    // or transport); every delta refreshes the felt-latency anchor read when
    // the reply's audio starts.
    if (msg.role == 'user' && !msg.isFinal) {
      final now = DateTime.now();
      if (_turnHeardAt == null) {
        _turnHeardAt = now;
        _log.info('turn: hearing you…');
      }
      _turnLastUserWordsAt = now;
    }

    // Remember the assistant's last final line (for echo detection) and log it.
    if (msg.role != 'user' && msg.isFinal) {
      _lastAssistantFinal = msg.text;
      if (msg.text.trim().isNotEmpty) _log.info('AI  : ${msg.text.trim()}');
    }

    // GLOBAL transcript guard (provider-agnostic — every AI's transcripts pass
    // here). For a FINAL user line, decide: REJECT (empty / duplicate / echo of
    // the assistant's own voice), REPLACE (a growing continuation of the same
    // utterance — some providers, e.g. Grok, emit it 2-3× as it builds, which
    // looked like the user "repeating"), or ACCEPT (a genuinely new line).
    if (msg.role == 'user' && msg.isFinal) {
      switch (_classifyUserFinal(msg.text)) {
        case _UserVerdict.reject:
          _dropTrailingUserPartial();
          return;
        case _UserVerdict.replace:
          _lastUserFinal = msg.text;
          _scheduleUserLog(msg.text);
          final cur = List<TranscriptEntry>.of(_state.transcripts);
          if (cur.isNotEmpty && cur.last.role == 'user') {
            cur[cur.length - 1] =
                cur.last.copyWith(text: msg.text, isFinal: true);
            _emit(_state.copyWith(transcripts: cur));
            return;
          }
        case _UserVerdict.accept:
          _lastUserFinal = msg.text;
          _scheduleUserLog(msg.text);
      }
    }

    final list = List<TranscriptEntry>.of(_state.transcripts);
    // Merge consecutive non-final fragments for the same role into one growing
    // line; otherwise append.
    if (list.isNotEmpty &&
        list.last.role == msg.role &&
        !list.last.isFinal) {
      list[list.length - 1] = list.last.copyWith(
        text: msg.text,
        isFinal: msg.isFinal,
      );
    } else {
      // A NEW user line is backdated to when the mic gate first heard the
      // voice — if that anchor is fresh (a stale one is leftover noise from
      // long before and would make the bubble look absurdly early).
      DateTime? spokeAt;
      if (msg.role == 'user') {
        final started = _utteranceStartAt;
        _utteranceStartAt = null; // consumed either way
        if (started != null &&
            DateTime.now().difference(started) < const Duration(seconds: 20)) {
          spokeAt = started;
        }
      }
      list.add(TranscriptEntry(
        role: msg.role,
        text: msg.text,
        isFinal: msg.isFinal,
        time: spokeAt,
      ));
    }
    if (list.length > _maxTranscripts) {
      list.removeRange(0, list.length - _maxTranscripts);
    }
    _emit(_state.copyWith(transcripts: list));
    // Autosave on every FINAL line: saving only at disconnect() lost the whole
    // conversation whenever the app was killed, the network dropped, or the
    // session expired (user-reported 2026-08-05). Debounced, and folded into
    // one history entry per live session by ChatHistoryStore.
    if (msg.isFinal) _scheduleHistorySave();
  }

  Timer? _historySaveTimer;

  /// Persist the running conversation shortly after the last final line —
  /// debounced so a fast exchange writes prefs once, not per line.
  void _scheduleHistorySave() {
    _historySaveTimer?.cancel();
    _historySaveTimer = Timer(const Duration(seconds: 3), () {
      unawaited(ChatHistoryStore.saveSession(_state.transcripts));
    });
  }

  // ---- Global transcript guard (provider-agnostic) -----------------------
  String _lastUserFinal = '';
  String _lastAssistantFinal = '';
  Timer? _userLogTimer;
  String _pendingUserLog = '';

  /// Classify a FINAL user line against the previous one + the assistant's last
  /// line. One place, same logic for gemini/openai/grok.
  _UserVerdict _classifyUserFinal(String text) {
    final norm = _normForCompare(text);
    if (norm.isEmpty) return _UserVerdict.reject;
    final last = _normForCompare(_lastUserFinal);
    if (last.isNotEmpty) {
      if (norm == last) return _UserVerdict.reject; // exact repeat
      if (norm.startsWith(last)) return _UserVerdict.replace; // growing
      if (last.startsWith(norm)) return _UserVerdict.reject; // shorter repeat
      if (_jaccard(text, _lastUserFinal) >= 0.9) return _UserVerdict.reject;
    }
    if (_lastAssistantFinal.isNotEmpty &&
        _jaccard(text, _lastAssistantFinal) >= 0.6) {
      return _UserVerdict.reject; // echo of the assistant's own voice
    }
    return _UserVerdict.accept;
  }

  /// Debounced log of the user's line: a growing utterance only logs ONCE — the
  /// final, complete text — instead of every incremental step.
  void _scheduleUserLog(String text) {
    _pendingUserLog = text.trim();
    _userLogTimer?.cancel();
    _userLogTimer = Timer(const Duration(milliseconds: 700), () {
      if (_pendingUserLog.isNotEmpty) _log.info('USER: $_pendingUserLog');
    });
  }

  void _dropTrailingUserPartial() {
    final cur = List<TranscriptEntry>.of(_state.transcripts);
    if (cur.isNotEmpty && cur.last.role == 'user' && !cur.last.isFinal) {
      cur.removeLast();
      _emit(_state.copyWith(transcripts: cur));
    }
  }

  String _normForCompare(String s) => s
      .toLowerCase()
      .replaceAll(RegExp(r'[^\w\s]'), ' ')
      .replaceAll(RegExp(r'\s+'), ' ')
      .trim();

  /// Word-set overlap (Jaccard) of two phrases — 1.0 identical, 0.0 disjoint.
  double _jaccard(String a, String b) {
    final sa = _normForCompare(a).split(' ').where((w) => w.isNotEmpty).toSet();
    final sb = _normForCompare(b).split(' ').where((w) => w.isNotEmpty).toSet();
    if (sa.isEmpty || sb.isEmpty) return 0;
    return sa.intersection(sb).length / sa.union(sb).length;
  }

  /// Cap on retained tool-activity rows, for the same reason as transcripts.
  static const int _maxTools = 40;

  void _applyToolCall(ToolCallMessage msg) {
    _log.info('tool → ${msg.name}(${msg.args})');
    final list = List<ToolActivity>.of(_state.tools)
      ..add(ToolActivity(
        id: msg.id,
        name: msg.name,
        args: msg.args,
        needsPermission: msg.needsPermission,
      ));
    if (list.length > _maxTools) {
      list.removeRange(0, list.length - _maxTools);
    }
    _emit(_state.copyWith(tools: list));

    // Client-executed tools: the model asks, the device acts.
    switch (msg.name) {
      case 'set_camera_zoom':
        final level = (msg.args['level'] as num?)?.toDouble();
        if (level != null) unawaited(setCameraZoom(level));
      case 'mute_mic':
        final muted = msg.args['muted'] as bool? ?? false;
        unawaited(muted ? stopListening() : startListening());
      case 'set_camera':
        final on = msg.args['on'] as bool? ?? true;
        unawaited(setCameraEnabled(on));
      case 'capture_photo':
        unawaited(captureGlassesPhoto());
      case 'identify_image':
        // The model reaches for this on "what is this?", and it needs a frame
        // to look at. Two ways to get one, depending on what is capturing:
        //
        // Glasses are photo-trigger — there is no stream, so snap one now and
        // the backend tool waits for it.
        //
        // The phone used to be streaming already, so this was a no-op for it.
        // It no longer is: the camera stays off until asked, and asking is
        // exactly what this tool call is. [grabFrame] starts it and waits for
        // the first frame, and the listener ships frames onward from there, so
        // the model gets its picture a beat later instead of never.
        unawaited(captureGlassesPhoto());
        if (_videoSource is! GlassesCaptureSource) {
          // If the camera cannot produce a frame (app backgrounded — Android
          // takes the camera away — or it's held elsewhere), say so NOW: the
          // backend tool is sitting in an 8 s frame wait and, without this,
          // times out and answers blind. grabFrame resolves within ~2 s
          // either way, so the precise failure beats the timeout by 6 s.
          unawaited(grabFrame().then((frame) {
            if (frame == null) {
              _log.warn('identify: no phone frame — reporting camera_off');
              _client.send(const CaptureFailedMessage(reason: 'camera_off'));
            }
          }).catchError((Object e) {
            _log.warn('identify: grabFrame failed ($e) — reporting camera_off');
            _client.send(const CaptureFailedMessage(reason: 'camera_off'));
          }));
        }
      case 'record_video':
        unawaited(startRecording(afterSpokenReply: true));
      case 'stop_recording':
        unawaited(stopRecording());
      case 'rotate_camera':
        unawaited(setCameraPortrait(!_state.cameraPortrait));
      case 'enable_bluetooth':
        unawaited(Future(() async {
          try {
            await _glassesBridge?.enableBluetooth();
          } catch (e) {
            _log.warn('enable_bluetooth failed: $e');
          }
        }));
      case 'connect_glasses':
        unawaited(_connectSavedGlasses());
      case 'disconnect_glasses':
        unawaited(Future(() async {
          try {
            await _glassesBridge?.disconnect();
          } catch (e) {
            _log.warn('disconnect_glasses failed: $e');
          }
        }));
      case 'end_session':
        // Let the spoken confirmation play out, then disconnect.
        Future<void>.delayed(
          const Duration(seconds: 3),
          () => unawaited(disconnect()),
        );
    }
  }

  void _applyToolResult(ToolResultMessage msg) {
    if (msg.ok) {
      _log.info('tool ✓ ${msg.name} → ${msg.result ?? "ok"}');
    } else {
      _log.warn('tool ✗ ${msg.name} → ${msg.error ?? "failed"}');
    }
    final list = List<ToolActivity>.of(_state.tools);
    final idx = list.indexWhere((t) => t.id == msg.id);
    if (idx >= 0) {
      list[idx] = list[idx].copyWith(
        ok: msg.ok,
        result: msg.result,
        error: msg.error,
      );
    } else {
      // Result without a prior call (shouldn't happen, but stay robust).
      list.add(ToolActivity(
        id: msg.id,
        name: msg.name,
        args: const {},
        ok: msg.ok,
        result: msg.result,
        error: msg.error,
      ));
    }
    _emit(_state.copyWith(tools: list));
    _applyReminder(msg);
    applyToolResultToCache(msg, _currentUserId());
    _applyOpenUrl(msg);
    _applyOpenMessaging(msg);

    // Voice flow: surface identify_image results so the UI can show the same
    // result sheet the scan button shows. The tool returns the full
    // {ok, mode, result} envelope as its payload. FAILED results stay out of
    // the stream: opening the Finder sheet pauses the live mic, and doing
    // that for a "couldn't get a fresh look" error left the session mute —
    // with the screen off the sheet is invisible and never dismissed, so the
    // mic never came back (device-proven 2026-07-11). Farry already speaks
    // the error; there is nothing to show.
    if (msg.name == 'identify_image' &&
        msg.result != null &&
        msg.result!['ok'] == true) {
      if (!_finderController.isClosed) {
        _finderController.add(FinderDetection.fromEnvelope(msg.result!));
      }
    }
  }

  /// Schedule, reschedule, or cancel the phone reminder for a task whose
  /// create/update/complete/delete tool just ran. The notification id is the
  /// backend task id so it stays in sync.
  void _applyReminder(ToolResultMessage msg) {
    if (!msg.ok) return;
    final res = msg.result;
    final id = (res?['id'] as num?)?.toInt();
    if (id == null) return;
    switch (msg.name) {
      case 'create_task':
      case 'update_task':
        final due = res?['due_date'] as String?;
        final title = (res?['title'] as String?) ?? 'Reminder';
        if (due != null && due.isNotEmpty) {
          final when = DateTime.tryParse(due);
          if (when != null) {
            unawaited(_scheduleAndReport(id: id, title: title, when: when));
          }
        }
      case 'complete_task':
      case 'delete_task':
        unawaited(Notifications.cancel(id));
    }
  }

  /// Schedule a reminder and, if it will not fire, say so in the transcript.
  ///
  /// Farry has already told the user "OK, I've set a reminder" by the time this
  /// runs — she writes the task server-side and cannot see the phone's
  /// notification settings. So the correction has to come from here, and it has
  /// to land right under her line where the user is already looking. Without
  /// it, the reminder silently never fires (device-proven 2026-07-19: task
  /// created, `dumpsys alarm` empty, Farry cheerful).
  Future<void> _scheduleAndReport({
    required int id,
    required String title,
    required DateTime when,
  }) async {
    final outcome = await Notifications.schedule(id: id, body: title, when: when);
    final notice = Notifications.noticeFor(outcome);
    if (notice == null) return;
    _emit(_state.copyWith(
      transcripts: [
        ..._state.transcripts,
        TranscriptEntry(role: 'notice', text: notice, isFinal: true),
      ],
    ));
  }

  /// Client-executed messaging: when a tool result asks to open a URL (a
  /// WhatsApp/Telegram deep link), open it so the user can send.
  void _applyOpenUrl(ToolResultMessage msg) {
    if (!msg.ok) return;
    final res = msg.result;
    if (res == null || res['action'] != 'open_url') return;
    final url = res['url'] as String?;
    if (url == null || url.isEmpty) return;
    final uri = Uri.tryParse(url);
    if (uri == null) return;
    // Telegram links can't pre-fill text, so the backend asks us to copy the
    // message to the clipboard — the user opens the chat then long-press →
    // Paste → Send (one tap instead of typing it out).
    final toCopy = res['copy_to_clipboard'] as String?;
    if (toCopy != null && toCopy.isNotEmpty) {
      unawaited(Clipboard.setData(ClipboardData(text: toCopy)));
      _emit(_state.copyWith(
        lastError: 'Message copied — in Telegram, long-press the box → Paste → '
            'Send.',
      ));
    }
    unawaited(_openExternal(uri));
  }

  /// CHANGED (UX Spec BUG 1 — the WhatsApp "send nahi hota" bug):
  /// When a contact was resolved on the DEVICE, the backend can't build a
  /// wa.me/sms link (it never sees the real number) — instead its tool result
  /// carries `action: "open_messaging"` + the opaque `contact_id`. We must open
  /// the messaging app for that id using the real number we kept locally
  /// (`_contactNumbers`), exactly like the typed `OpenMessagingMessage` path.
  ///
  /// Previously ONLY `action: "open_url"` was handled, so this device-contact
  /// path (the common "WhatsApp Sara" flow) silently did nothing and WhatsApp
  /// never opened. This routes it to the same `_handleOpenMessaging` logic.
  void _applyOpenMessaging(ToolResultMessage msg) {
    if (!msg.ok) return;
    final res = msg.result;
    if (res == null || res['action'] != 'open_messaging') return;
    final contactId = res['contact_id'] as String?;
    if (contactId == null || contactId.isEmpty) return;
    final channel = (res['channel'] as String?) ?? 'whatsapp';
    final message = (res['message'] as String?) ?? '';
    unawaited(_handleOpenMessaging(
      OpenMessagingMessage(
        channel: channel,
        contactId: contactId,
        message: message,
      ),
    ));
  }

  Future<void> _openExternal(Uri uri) async {
    try {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    } catch (e) {
      _log.warn('open external url failed: $e');
    }
  }

  /// Open Stripe Checkout for [plan] in the browser. Returns a message to show
  /// the user when it can't start — null on success.
  ///
  /// Kept on the controller because the reconnect overlay (where the cap notice
  /// lives) already talks to it, and it holds the config with the auth token.
  /// A null URL from the API means checkout isn't available yet (Stripe not
  /// configured, or a transient error) — we tell the user rather than opening a
  /// blank tab.
  Future<String?> startUpgrade(String plan) async {
    final api = DataApi(_config);
    try {
      final url = await api.createCheckout(plan);
      if (url == null) {
        return "Upgrades aren't available just yet. Please try again later.";
      }
      await _openExternal(Uri.parse(url));
      return null;
    } on SessionExpiredException {
      return 'Please sign in again to upgrade.';
    } catch (e) {
      _log.warn('start upgrade failed: $e');
      return "Couldn't start the upgrade. Please try again.";
    } finally {
      api.dispose();
    }
  }

  // ---- Privacy-preserving contact resolution (round-trip) ----------------
  //
  // The backend asks us to resolve a NAME against the phone's own contacts and
  // we reply with MASKED numbers + opaque per-session ids. The real number is
  // kept only here (never sent to the server); when the user confirms, the
  // backend sends `open_messaging` with the id and we open WhatsApp/SMS using
  // the locally-stored number.

  /// Opaque contactId -> real phone number, for this session only.
  final Map<String, String> _contactNumbers = {};
  int _contactIdSeq = 0;

  /// The backend asked us to find a contact by name. Match locally, mask the
  /// numbers, and reply — never auto-send.
  Future<void> _handleResolveContactRequest(
    ResolveContactRequestMessage req,
  ) async {
    Future<void> reply(String status,
        [List<Map<String, dynamic>> candidates = const []]) async {
      _client.send(ResolveContactResultMessage(
        requestId: req.requestId,
        status: status,
        candidates: candidates,
      ));
    }

    try {
      if (!await FlutterContacts.requestPermission(readonly: true)) {
        await reply('permission_denied');
        return;
      }
      final matches = await _findContactsByName(req.name);
      // Collapse to distinct numbers, minting an id for each.
      final seen = <String>{};
      final candidates = <Map<String, dynamic>>[];
      for (final c in matches) {
        final raw = _firstPhone([c]);
        if (raw == null) continue;
        final clean = _normalizePhone(raw);
        if (clean.isEmpty || seen.contains(clean)) continue;
        seen.add(clean);
        final id = 'c${_contactIdSeq++}';
        _contactNumbers[id] = clean;
        final cand = <String, dynamic>{
          'contactId': id,
          'displayName': c.displayName,
          'maskedNumber': _maskPhone(clean),
          // The real number, for EVERY channel — not just telegram. Telegram
          // dials it server-side from the user's own account, and including it
          // everywhere is what lets a contact resolved for ONE app be reused on
          // another without re-resolving (a resolve per channel mints different
          // ids, and the telegram send then has no number for the id the user
          // picked). It only ever reaches the user's own backend, held in
          // memory for the session.
          'phone': '+$clean',
        };
        candidates.add(cand);
      }
      if (candidates.isEmpty) {
        await reply(matches.isEmpty ? 'not_found' : 'no_number');
      } else if (candidates.length == 1) {
        await reply('found', candidates);
      } else {
        await reply('ambiguous', candidates);
      }
    } catch (e) {
      _log.warn('resolve contact request failed: $e');
      await reply('not_found');
    }
  }

  /// The user confirmed — open the messaging app for a resolved contact id,
  /// using the real number we kept locally.
  Future<void> _handleOpenMessaging(OpenMessagingMessage msg) async {
    final number = _contactNumbers[msg.contactId];
    if (number == null || number.isEmpty) {
      _emit(_state.copyWith(
        lastError: "Couldn't open that contact — try again.",
      ));
      return;
    }
    final body = Uri.encodeComponent(msg.message);
    final uri = Uri.parse(msg.channel == 'sms'
        ? 'sms:+$number?body=$body'
        : 'https://wa.me/$number?text=$body');
    await _openExternal(uri);
  }

  /// Contacts whose display name matches [name] (case-insensitive), exact
  /// matches first so "Sara" prefers a contact literally named Sara.
  Future<List<Contact>> _findContactsByName(String name) async {
    final q = name.toLowerCase().trim();
    if (q.isEmpty) return const [];
    final all = await FlutterContacts.getContacts(withProperties: true);
    final hits = all.where((c) => _searchText(c).contains(q)).toList()
      ..sort((a, b) {
        // Exact display-name match ranks first, then a name that starts with
        // the query, then the rest.
        int rank(Contact c) {
          final dn = c.displayName.toLowerCase();
          if (dn == q) return 0;
          if (dn.startsWith(q)) return 1;
          return 2;
        }

        return rank(a).compareTo(rank(b));
      });
    return hits;
  }

  /// All the fields we match a spoken name against: display name, first/last,
  /// nickname, and company — so "Ahmed Office" or a business name resolves too.
  String _searchText(Contact c) {
    final parts = <String>[
      c.displayName,
      c.name.first,
      c.name.last,
      c.name.nickname,
      for (final o in c.organizations) o.company,
    ];
    return parts.where((s) => s.isNotEmpty).join(' ').toLowerCase();
  }

  String? _firstPhone(List<Contact> contacts) {
    for (final c in contacts) {
      for (final p in c.phones) {
        if (p.number.trim().isNotEmpty) return p.number;
      }
    }
    return null;
  }

  /// Digits-only number with a country code. Mirrors the backend's
  /// normalize_phone; falls back to UAE (971) only when NO code is present.
  ///
  /// A contact saved as "+91 98765…" already carries its country code — the
  /// old version stacked 971 on top of it and every WhatsApp to a non-UAE
  /// contact went to a number that doesn't exist (user-reported 2026-08-27).
  /// "+" and the "00" dial prefix are how a number declares its own code.
  String _normalizePhone(String phone, {String defaultCc = '971'}) {
    final raw = phone.trim();
    final digits = raw.replaceAll(RegExp(r'\D'), '');
    if (digits.isEmpty) return '';
    if (raw.startsWith('+')) return digits;
    if (digits.startsWith('00')) return digits.substring(2);
    if (digits.startsWith(defaultCc)) return digits;
    return defaultCc + digits.replaceFirst(RegExp(r'^0+'), '');
  }

  /// A read-aloud masked number, e.g. `+971 ••• ••67` — hides the middle so the
  /// assistant can confirm by ear without exposing the full number.
  String _maskPhone(String digits) {
    if (digits.length < 5) return '••${digits.length >= 2 ? digits.substring(digits.length - 2) : digits}';
    return '+${digits.substring(0, 3)} ••• ••${digits.substring(digits.length - 2)}';
  }

  // ---- Mic (push-to-talk / toggle) --------------------------------------

  /// Open the mic: barge-in if the assistant is speaking, announce
  /// `audio_start`, and begin streaming PCM.
  Future<void> startListening() async {
    if (_state.micOpen) return;

    // Barge-in: if TTS is playing, stop it locally and tell the server.
    if (_state.liveState == LiveState.speaking) {
      _log.info('event: user barge-in (interrupted AI)');
      await interrupt();
    }

    // Open the activity window BEFORE audio flows so the backend's manual VAD
    // counts the very first words (audio sent before audio_start is dropped).
    _log.info('event: mic opened (listening)');
    _client.send(const AudioStartMessage());
    await _startAudio();
    _emit(_state.copyWith(micOpen: true, liveState: LiveState.listening));
  }

  /// Close the mic and announce `audio_stop`.
  Future<void> stopListening() async {
    if (!_state.micOpen) return;
    _log.info('event: mic closed');
    await _stopAudio();
    _client.send(const AudioStopMessage());
    _emit(_state.copyWith(
      micOpen: false,
      liveState:
          _state.liveState == LiveState.listening ? LiveState.thinking : null,
    ));
  }

  /// Toggle the mic open/closed.
  Future<void> toggleMic() =>
      _state.micOpen ? stopListening() : startListening();

  /// Barge-in: stop assistant playback now and notify the server.
  Future<void> interrupt() async {
    // Re-open the mic immediately — playback is being cut short.
    _ttsClear?.cancel();
    _ttsActive = false;
    _client.send(const InterruptMessage());
    await _player.flush();
    if (_state.liveState == LiveState.speaking) {
      _emit(_state.copyWith(liveState: LiveState.idle));
    }
  }

  /// Send a typed text turn (no mic).
  void sendText(String text) {
    final trimmed = text.trim();
    if (trimmed.isEmpty) return;
    _client.send(TextMessage(trimmed));
    // Optimistically show the user's line.
    final list = List<TranscriptEntry>.of(_state.transcripts)
      ..add(TranscriptEntry(role: 'user', text: trimmed, isFinal: true));
    var next = _state.copyWith(transcripts: list);
    // Cross-verification: when the camera is on, surface the EXACT frame the
    // model will look at for this turn as a chat preview, so the user can
    // confirm the answer matches what was actually captured — the diagnostic
    // for "it answered about the wrong / an old image" (e.g. after a camera
    // flip). Mirrors the glasses one-shot preview, but on demand for the phone
    // camera (which streams ~1 fps and would otherwise never show a preview).
    if (_state.cameraOn && _lastFrame != null) {
      next = next.copyWith(
        lastCapturedPhoto: _lastFrame,
        lastCapturedAt: DateTime.now(),
      );
      _saveCaptureToGallery(_lastFrame!);
    }
    _emit(next);
  }

  /// Last capture written to the gallery — dedup so the same frame object isn't
  /// saved twice (e.g. two text turns without a fresh frame in between).
  Uint8List? _lastGallerySaved;

  /// Save a capture (phone frame or glasses still) to the phone gallery. The
  /// image the user "clicked" to identify is otherwise in-memory only. Fire and
  /// forget — a gallery failure must never disrupt the live session.
  void _saveCaptureToGallery(Uint8List jpeg) {
    if (!_config.saveCapturesToGallery) return;
    if (jpeg.isEmpty || identical(jpeg, _lastGallerySaved)) return;
    _lastGallerySaved = jpeg;
    unawaited(MediaSaver.saveImage(jpeg));
  }

  /// Respond to a tool-permission gate.
  void respondToolPermission(String id, bool granted) {
    _client.send(ToolPermissionMessage(id: id, granted: granted));
  }

  // ---- Capture stream piping --------------------------------------------

  Future<void> _startAudio() async {
    await _audioSource.startAudio();
    await _audioSub?.cancel();
    _audioSub = _audioSource.audio16k.listen((pcm) {
      // Half-duplex echo guard. Two independent conditions, either of which
      // keeps the mic shut:
      //   * _ttsActive — the server says this turn is still speaking;
      //   * the player's own drain clock — audio fed but not yet played out,
      //     plus a tail for speaker decay / room ring-down.
      // The clock is authoritative: the old timer-only version under-ran
      // whenever playback started later than the first byte or a turn emitted
      // several audio_starts, and the assistant's voice leaked back in as a
      // bogus user turn (device-proven 2026-08-05).
      if (_ttsActive || _player.isPlayingWithin(_ttsTail)) {
        _micGate.reset(); // re-learn the room after the speaker goes quiet
        return;
      }
      // Energy gate: hold back non-speech so the provider's turn detector
      // never sees it and the transcriber can't invent words for it.
      List<Uint8List> toSend;
      try {
        toSend = _micGate.process(pcm);
      } catch (e) {
        // Belt and braces: an unhandled throw here once cancelled the whole
        // mic subscription and the assistant went permanently deaf. The mic
        // outranks the gate — on any doubt, send.
        _log.warn('mic gate failed, sending unfiltered: $e');
        toSend = [pcm];
      }
      for (final chunk in toSend) {
        _client.sendAudio(chunk);
      }
    }, onError: (Object e) => _log.warn('mic stream error: $e'));
  }

  Future<void> _stopAudio() async {
    await _audioSub?.cancel();
    _audioSub = null;
    await _audioSource.stopAudio();
  }

  Future<void> _startVideo() async {
    await _attachFrameListener();
    await _videoSource.startVideo();
    // Only here, not in [_attachFrameListener]: listening for frames is not
    // the same as the camera being on, and a one-shot must not light this up.
    _emit(_state.copyWith(cameraOn: true));
  }

  /// Subscribe to the source's frames WITHOUT starting a stream.
  ///
  /// Split out because a one-shot capture needs somewhere for its frame to
  /// land. The listener used to be attached only by [_startVideo], so once the
  /// camera stopped opening with the session, a `captureOnce()` frame was
  /// emitted into a stream nobody was listening to and the scan button got
  /// nothing back — caught by the test, not by a device.
  Future<void> _attachFrameListener() async {
    await _videoSub?.cancel();
    // A glasses source only emits on an explicit capture (photo-trigger), so
    // its frames are one-shot and must always be sent. A phone camera streams
    // ~1 fps continuously — those we drop while the assistant speaks.
    final oneShotCamera = _videoSource is GlassesCaptureSource;
    _videoSub = _videoSource.jpegFrames.listen((jpeg) {
      // Always keep the freshest frame for the scan button / identify_image,
      // even while the assistant is speaking.
      _lastFrame = jpeg;
      // Every DELIBERATE capture is shown in the chat, so the user can see
      // exactly what was sent for recognition. That was the diagnostic for
      // "it described the wrong scene" on the glasses, and it is just as
      // useful on the phone now that a question takes one picture: what the
      // model was looking at stops being a guess.
      //
      // Only one-shots. A continuous stream is ~1 fps and would bury the
      // conversation under a filmstrip.
      if (oneShotCamera || _oneShotPending) {
        _emit(_state.copyWith(
          lastCapturedPhoto: jpeg,
          lastCapturedAt: DateTime.now(),
        ));
      }
      if (oneShotCamera) {
        // Glasses only. A glasses still is the sole copy once the glasses'
        // own storage is cleared; a phone capture is a passing look at
        // something the user is holding, and filling their gallery with those
        // would be a surprise nobody asked for.
        _saveCaptureToGallery(jpeg);
      }
      // Don't feed CONTINUOUS frames while the assistant is speaking: they
      // can't influence the in-flight reply and would only pile up in the
      // realtime model's context. A one-shot is the opposite — somebody asked
      // for that exact picture and something is waiting on it, so it MUST get
      // through even mid-speech. Otherwise "what am I looking at?" makes the
      // model start talking, the talking drops the frame, and it answers blind.
      //
      // "One-shot" used to mean only the glasses. The phone takes single
      // frames now too (see [grabFrame]), and they arrive at the worst
      // possible moment: the model calls identify_image, starts narrating, and
      // the narration was throwing away the picture it had just asked for.
      // Measured on an S23, 2026-08-16: the tool waited its full 8 s and timed
      // out with a camera that had captured perfectly.
      final oneShot = oneShotCamera || _oneShotPending;
      if (_oneShotPending) _oneShotPending = false;
      if (_ttsActive && !oneShot) return;
      _client.sendVideo(jpeg);
    });
  }

  Future<void> _stopVideo() async {
    await _videoSub?.cancel();
    _videoSub = null;
    // Drop the cached frame so a later scan/identify can't run against a stale
    // scene from before the camera was turned off.
    _lastFrame = null;
    await _videoSource.stopVideo();
    _emit(_state.copyWith(cameraOn: false));
  }

  /// B3: snap a still from the active glasses camera and push it into the
  /// video pipeline (→ Gemini vision + backend last_frame). Triggered by the
  /// `capture_photo` voice tool or the on-screen shutter button. No-op if the
  /// selected camera isn't the glasses (the phone camera already streams).
  ///
  /// A failed capture is reported to the backend as a `capture_failed`
  /// control message, so the waiting vision tool answers immediately with the
  /// precise cause (glasses not connected / busy / transfer stalled) instead
  /// of running out its frame timeout.
  Future<void> captureGlassesPhoto() async {
    final src = _videoSource;
    if (src is! GlassesCaptureSource) {
      _log.warn('capture_photo: active camera is not the glasses — ignoring');
      return;
    }
    // Make sure the jpegFrames→sendVideo listener is attached so the photo
    // reaches Gemini (glasses have no continuous stream to start it for us).
    if (_videoSub == null) await _startVideo();
    final result = await src.capturePhoto();
    if (!result.ok) {
      final reason = result.failure!.wire;
      _log.warn('glasses capture failed ($reason) — reporting to backend');
      _client.send(CaptureFailedMessage(reason: reason));
      return;
    }
    // Insurance: if the frame pipe was torn down while the capture was in
    // flight (session teardown races, legacy background handling), the
    // listener that ships frames no longer exists — ship the photo directly
    // so it still reaches the model. No double-send: when the listener IS
    // attached, it does the sending and this branch is skipped.
    final jpeg = result.jpeg;
    if (_videoSub == null && jpeg != null) {
      _log.warn('glasses photo arrived with no frame pipe — sending directly');
      _lastFrame = jpeg;
      _client.sendVideo(jpeg);
      _saveCaptureToGallery(jpeg); // listener skipped → save it here
    }
  }

  /// Enable/disable the camera stream at runtime.
  Future<void> setCameraEnabled(bool enabled) async {
    _cameraDesired = enabled;  // the user's intent, kept across reconnects
    if (enabled == _state.cameraOn) return;
    if (enabled) {
      await _startVideo();
    } else {
      await _stopVideo();
    }
  }

  // ---- App lifecycle (background/foreground) -----------------------------

  /// Whether the USER wants the camera on. Survives a background release and a
  /// slow reconnect, so we can tell "the OS took it, put it back" apart from
  /// "the user turned it off, leave it off".
  bool _cameraDesired = false;

  /// Whether the app is in the foreground. The camera can only be (re)opened
  /// while it is, so reconciliation waits for this.
  bool _foreground = true;

  /// Re-open the camera when it SHOULD be on but isn't — e.g. it was released
  /// on a background and we are foreground + connected again.
  ///
  /// Called from BOTH the foreground event and session-ready, because a slow
  /// connect can finish AFTER the user has already returned: the foreground
  /// handler then ran too early (still connecting) and couldn't restart it, and
  /// nothing else would. That left the camera dead with the preview seemingly
  /// up, and every scan answering "I can't see a current camera frame"
  /// (on-device 2026-07-03: a ~70 s connect). Idempotent.
  Future<void> _ensureCameraMatchesIntent() async {
    if (_cameraDesired &&
        _foreground &&
        _state.connection == ConnectionStatus.connected &&
        !_state.cameraOn) {
      await _startVideo();
    }
  }

  /// App went to the background: the OS invalidates the camera, so fully
  /// release it (a frozen dead controller is exactly the "camera stuck" hang).
  ///
  /// GLASSES EXCEPTION: the glasses camera is an external BLE device — the OS
  /// does not invalidate it on background/screen-off, and tearing the pipe
  /// down here drops a one-shot photo that lands moments later. Hit on-device
  /// 2026-07-11: the screen turned off between the shutter and the thumbnail
  /// (~4 s), the frame arrived with no listener attached, and the model
  /// answered "couldn't get a fresh look" despite a perfect capture.
  Future<void> handleAppBackground() async {
    // Track this even for glasses, so the flag always reflects reality.
    _foreground = false;
    if (_videoSource is GlassesCaptureSource) return;
    if (_state.cameraOn) {
      await _stopVideo();
      await _videoSource.releaseCamera();
      _emit(_state.copyWith(cameraOn: false));
    }
  }

  /// App returned to the foreground: re-open the camera if the user wants it on
  /// and the session is connected. If we're still connecting, session-ready
  /// retries this reconciliation.
  Future<void> handleAppForeground() async {
    _foreground = true;
    await _ensureCameraMatchesIntent();
  }

  /// Switch the camera preview/capture between portrait and landscape.
  Future<void> setCameraPortrait(bool portrait) async {
    if (portrait == _state.cameraPortrait) return;
    await _videoSource.setPortrait(portrait);
    _emit(_state.copyWith(cameraPortrait: portrait));
  }

  /// Flip the phone camera between the back and front (selfie) lens. No-op for
  /// glasses (single fixed lens). Zoom resets to 1× since the new lens has its
  /// own zoom range.
  Future<void> setCameraFront(bool front) async {
    if (front == _state.cameraFront) return;
    await _videoSource.setFrontCamera(front);
    _emit(_state.copyWith(cameraFront: front, cameraZoom: 1.0));
  }

  /// Zoom the camera and reflect the applied level in state (drives the UI
  /// read-out and presets). Used by pinch, the preset chips, and the model's
  /// `set_camera_zoom` tool.
  Future<void> setCameraZoom(double level) async {
    final applied = await _videoSource.setZoom(level);
    _emit(_state.copyWith(cameraZoom: applied));
  }

  // ---- Device switching (universal adapter, B1-B: per-channel) ----------

  /// Select the microphone device (phone/earbuds ⇄ glasses). Restarts only the
  /// audio stream; the camera is untouched. The socket stays up; the next
  /// `hello` advertises the new combo.
  Future<void> setAudioDevice(CaptureDeviceKind kind) async {
    if (kind == _registry.audioKind) return;
    _log.info('audio device → $kind');
    final wasListening = _state.micOpen;
    await _stopAudio();
    _registry.setAudioKind(kind);
    await _audioSource.initialize();
    _watchGlassesStatus();
    if (wasListening) await _startAudio();
    _emit(_state.copyWith(audioKind: kind.name));
    _notifyDeviceUpdate();
  }

  /// Select the camera device (phone ⇄ glasses). Restarts only the video
  /// stream; audio is untouched.
  Future<void> setVideoDevice(CaptureDeviceKind kind) async {
    if (kind == _registry.videoKind) return;
    _log.info('video device → $kind');
    final wasOn = _state.cameraOn;
    await _stopVideo();
    _registry.setVideoKind(kind);
    await _videoSource.initialize();
    if (wasOn) await _startVideo();
    _emit(_state.copyWith(videoKind: kind.name));
    // Tell the backend the camera changed so it re-picks the frame-wait
    // budget: glasses connect AFTER hello (voice flow), so the photo-trigger
    // camera is selected mid-session and the hello-time budget is wrong.
    _notifyDeviceUpdate();
  }

  /// Send the backend the current audio/video device combo so it can size the
  /// frame-wait budget correctly (photo-trigger glasses need longer than a
  /// streaming phone camera). Sent on every device switch; no-op if the socket
  /// is down (a fresh `hello` will carry the combo on reconnect anyway).
  void _notifyDeviceUpdate() {
    _client.send(DeviceUpdateMessage(
      videoKind: _registry.videoKind.name,
      audioKind: _registry.audioKind.name,
    ));
  }

  // ---- Config / reconnect target ----------------------------------------

  /// Point the client at a new backend (settings change) and reconnect.
  void updateConfig(AppConfig config) {
    _config = config;
    _client.updateConfig(config);
    // Apply the glasses storage-retention choice immediately. Pushed
    // unconditionally (not gated on a live connection): it just updates a field
    // on the native SDK, so it must land whenever the user changes the Setting —
    // even if the glasses are currently linked only via the Glasses Lab.
    unawaited(
      _glassesBridge?.setRetentionDays(config.glassesRetentionDays) ??
          Future<void>.value(),
    );
  }

  AppConfig get config => _config;

  /// Clear a transient error banner.
  void dismissError() => _emit(_state.copyWith(clearError: true));

  // ---- Disposal ----------------------------------------------------------

  Future<void> dispose() async {
    await _connectivitySub?.cancel();
    _historySaveTimer?.cancel();
    _locationTimer?.cancel();
    await ChatHistoryStore.saveSession(_state.transcripts);
    _ttsClear?.cancel();
    _userLogTimer?.cancel();
    _autoSyncTimer?.cancel();
    _autoSyncWatchdog?.cancel();
    _recordingTicker?.cancel();
    _pendingMediaTimer?.cancel();
    await _audioSub?.cancel();
    await _videoSub?.cancel();
    await _glassesSub?.cancel();
    await _wearSub?.cancel();
    await _eventSub?.cancel();
    await _frameSub?.cancel();
    await _statusSub?.cancel();
    await _client.dispose();
    await _player.dispose();
    await _registry.dispose();
    await _stateController.close();
    await _finderController.close();
  }
}
