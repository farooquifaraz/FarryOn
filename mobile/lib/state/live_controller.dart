import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart' show Clipboard, ClipboardData;
import 'package:flutter_contacts/flutter_contacts.dart';
import 'package:url_launcher/url_launcher.dart';

import '../capture/capture_source.dart';
import '../capture/device_registry.dart';
import '../capture/glasses_capture_source.dart';
import '../core/cache_patch.dart';
import '../core/chat_history.dart';
import '../core/config.dart';
import '../core/location.dart';
import '../core/log_store.dart';
import '../core/logger.dart';
import '../core/media_saver.dart';
import '../core/notifications.dart';
import '../data/finder_api.dart';
import '../data/live_client.dart';
import '../features/glasses_lab/bridge/glasses_channel.dart';
import '../playback/pcm_player.dart';
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
  bool _ttsActive = false;
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

  // ---- Observable state --------------------------------------------------

  final _stateController =
      StreamController<LiveSessionState>.broadcast(sync: false);

  /// Stream of state snapshots for the UI.
  Stream<LiveSessionState> get stateStream => _stateController.stream;

  // Latest camera JPEG frame, kept so the live-scan button and (via the cached
  // server-side frame) the identify_image tool can inspect the current view.
  Uint8List? _lastFrame;

  /// The most recent camera frame (raw JPEG), or null if the camera is off.
  Uint8List? get lastFrame => _lastFrame;

  /// The freshest camera frame, waiting up to ~2s if the camera just turned on
  /// or resumed and the first frame (~1 fps) hasn't arrived yet — so the scan
  /// button doesn't wrongly report "no camera frame".
  Future<Uint8List?> grabFrame() async {
    if (_lastFrame != null) return _lastFrame;
    if (!_state.cameraOn) return null;
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
      await _scanAndConnectBest(bridge, triedMac);
    });
  }

  /// Scan and connect the best-available glasses: powered-on-now wins, then a
  /// live BLE advertiser, then anything we saw. [skipMac] is the unit we just
  /// failed to reach (avoid retrying the dead one first).
  Future<void> _scanAndConnectBest(
      GlassesBridgeApi bridge, String? skipMac) async {
    final hits = await bridge.scan(timeout: const Duration(seconds: 6));
    if (hits.isEmpty) {
      _log.warn('connect_glasses: no glasses found — turn them on');
      return;
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
        _emit(_state.copyWith(glassesConnected: connected));
        // The connect attempt has resolved (either way) — release the in-flight
        // guard so a later reconnect (new session, or after a drop) can proceed.
        _connectingGlasses = false;
        // Push the storage-retention policy to the freshly-connected glasses so
        // synced photos are pruned per the user's Settings choice.
        if (connected) {
          unawaited(
            _glassesBridge?.setRetentionDays(_config.glassesRetentionDays) ??
                Future<void>.value(),
          );
          // Pull anything already sitting on the glasses (photos taken while
          // disconnected) shortly after the link is up.
          _scheduleAutoGlassesSync(const Duration(seconds: 3));
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
          _scheduleAutoGlassesSync(const Duration(seconds: 8));
        }
      case 'syncProgress':
        // A sync run reaching 100% (files done / nothing to sync) frees the
        // in-flight guard so the next photo can trigger a fresh sync.
        final pct = (event.data['pct'] as num?)?.toInt() ?? 0;
        if (pct >= 100) {
          _autoSyncing = false;
          _autoSyncWatchdog?.cancel();
        }
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
    _autoSyncWatchdog?.cancel();
    _autoSyncWatchdog = Timer(const Duration(seconds: 90), () {
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
    ));
    if (outcome != PermissionOutcome.granted) {
      _log.warn('permissions not granted: $outcome');
      return outcome;
    }

    await _player.initialize();
    await _audioSource.initialize();
    _watchGlassesStatus();
    // If the camera is a different device, initialize it too (same instance is
    // idempotent, so a double-init when both channels share a source is safe).
    if (!identical(_videoSource, _audioSource)) {
      await _videoSource.initialize();
    }
    // Start the camera immediately so the preview is live and ~1 fps frames
    // begin flowing. Record the intent so a background release or a slow
    // reconnect can restore it (see _ensureCameraMatchesIntent).
    _cameraDesired = true;
    _foreground = true;
    await _startVideo();

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

  /// Resolve the current location and send it to the backend (best-effort).
  Future<void> _pushLocation() async {
    try {
      final fix = await LocationService.current();
      if (fix != null) _client.send(LocationUpdateMessage(fix.toJson()));
    } catch (e) {
      _log.warn('push location failed: $e');
    }
  }

  /// Tear down capture, playback, and the socket (keeps objects reusable).
  Future<void> disconnect() async {
    // Persist the conversation before tearing down so the user can revisit it.
    unawaited(ChatHistoryStore.saveSession(_state.transcripts));
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
      case TranscriptMessage():
        _applyTranscript(msg);
      case AudioStartEvent():
        // Assistant begins speaking — mute the mic until playback drains.
        _log.info('event: AI started speaking');
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
        _emit(_state.copyWith(lastError: msg.message));
      case PongMessage():
        break; // handled inside the client (heartbeat)
      case ResolveContactRequestMessage():
        _log.info('event: resolve contact "${msg.name}" (${msg.channel})');
        unawaited(_handleResolveContactRequest(msg));
      case OpenMessagingMessage():
        unawaited(_handleOpenMessaging(msg));
      case UnknownServerMessage():
        _log.debug('unknown server message: ${msg.type}');
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
      list.add(TranscriptEntry(
        role: msg.role,
        text: msg.text,
        isFinal: msg.isFinal,
      ));
    }
    if (list.length > _maxTranscripts) {
      list.removeRange(0, list.length - _maxTranscripts);
    }
    _emit(_state.copyWith(transcripts: list));
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
        // The model often reaches for identify_image on "what is this". With
        // the glasses (photo-trigger) there's no live frame, so snap one now —
        // the backend tool waits for it. No-op if the phone camera is active
        // (it already streams frames).
        unawaited(captureGlassesPhoto());
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
  /// normalize_phone; falls back to UAE (971) when no code is present.
  String _normalizePhone(String phone, {String defaultCc = '971'}) {
    final digits = phone.replaceAll(RegExp(r'\D'), '');
    if (digits.isEmpty) return '';
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
      // Half-duplex: never feed the mic while the assistant's TTS is still
      // playing out (covers the player's buffer drain after audio_end), so
      // automatic VAD can't re-trigger on its own voice.
      if (_ttsActive) return;
      _client.sendAudio(pcm);
    });
  }

  Future<void> _stopAudio() async {
    await _audioSub?.cancel();
    _audioSub = null;
    await _audioSource.stopAudio();
  }

  Future<void> _startVideo() async {
    await _videoSource.startVideo();
    await _videoSub?.cancel();
    // A glasses source only emits on an explicit capture (photo-trigger), so
    // its frames are one-shot and must always be sent. A phone camera streams
    // ~1 fps continuously — those we drop while the assistant speaks.
    final oneShotCamera = _videoSource is GlassesCaptureSource;
    _videoSub = _videoSource.jpegFrames.listen((jpeg) {
      // Always keep the freshest frame for the scan button / identify_image,
      // even while the assistant is speaking.
      _lastFrame = jpeg;
      // Glasses photos are one-shots: surface each captured frame as a chat
      // preview so the user can see EXACTLY what was sent for recognition
      // (confirms the photo matches where the glasses point — the diagnostic
      // for "it described the wrong scene"). Phone frames stream ~1 fps, so we
      // don't spam the preview with those.
      if (oneShotCamera) {
        _emit(_state.copyWith(
          lastCapturedPhoto: jpeg,
          lastCapturedAt: DateTime.now(),
        ));
        // A glasses still is a deliberate capture — save it to the gallery so
        // it isn't lost when the glasses' own storage is later cleared.
        _saveCaptureToGallery(jpeg);
      }
      // Don't feed CONTINUOUS frames while the assistant is speaking: they
      // can't influence the in-flight reply and would only pile up in the
      // realtime model's context. But a glasses photo is a one-shot the model
      // explicitly requested — it MUST reach the model even mid-speech, else a
      // "take a photo and tell me about it" (model narrates → TTS active) drops
      // the frame and comes back with no information.
      if (_ttsActive && !oneShotCamera) return;
      _client.sendVideo(jpeg);
    });
    _emit(_state.copyWith(cameraOn: true));
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
    await ChatHistoryStore.saveSession(_state.transcripts);
    _ttsClear?.cancel();
    _userLogTimer?.cancel();
    _autoSyncTimer?.cancel();
    _autoSyncWatchdog?.cancel();
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
