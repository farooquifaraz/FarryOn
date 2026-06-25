import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_contacts/flutter_contacts.dart';
import 'package:url_launcher/url_launcher.dart';

import '../capture/capture_source.dart';
import '../capture/device_registry.dart';
import '../core/chat_history.dart';
import '../core/config.dart';
import '../core/location.dart';
import '../core/logger.dart';
import '../core/notifications.dart';
import '../data/finder_api.dart';
import '../data/live_client.dart';
import '../playback/pcm_player.dart';
import '../protocol/frames.dart';
import '../protocol/messages.dart';
import '../protocol/protocol.dart';
import 'live_state.dart';
import 'permissions.dart';

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
  })  : _config = config,
        _registry = registry,
        _player = player,
        _permissions = permissions,
        platform = platform ?? defaultPlatform {
    _client = clientFactory(_config, _activeDeviceInfo);
    _bindClient();
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
  // CHANGED (UX Spec BUG 3 / latency): reduced 1200 -> 450 ms. The old 1.2s
  // tail created a long dead-window after every reply where the user's next
  // words weren't captured ("my voice processes slowly"). 450 ms still covers
  // the OS buffer drain + ring-down because the on-device acoustic echo
  // cancellation (enableVoiceProcessing, see PhoneCaptureSource) already
  // suppresses the assistant's own voice. If you ever hear the assistant echo
  // itself back as a "user" line on a specific device, nudge this up to ~700.
  static const int _ttsTailMarginMs = 450;

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

  CaptureSource get _activeSource => _registry.active;
  DeviceInfo _activeDeviceInfo() => _activeSource.info;

  // ---- Lifecycle ---------------------------------------------------------

  /// Acquire permissions, prepare the audio engine + capture device, and open
  /// the socket. Returns the permission outcome so the UI can show rationale.
  Future<PermissionOutcome> connect() async {
    final outcome = await _permissions.requestMicAndCamera();
    _emit(_state.copyWith(
      permissionsGranted: outcome == PermissionOutcome.granted,
      deviceKind: _registry.activeKind.name,
    ));
    if (outcome != PermissionOutcome.granted) {
      _log.warn('permissions not granted: $outcome');
      return outcome;
    }

    await _player.initialize();
    await _activeSource.initialize();
    // Start the camera immediately so the preview is live and ~1 fps frames
    // begin flowing.
    await _startVideo();

    _client.start();
    // Hands-free: open the mic right away so the user can just talk. The mic
    // button is a mute toggle, and we never feed the mic while the assistant
    // is speaking (half-duplex, see [_startAudio]).
    await startListening();
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
    _emit(_state.copyWith(
      micOpen: false,
      cameraOn: false,
      liveState: LiveState.idle,
    ));
  }

  // ---- Client event wiring ----------------------------------------------

  void _bindClient() {
    _statusSub = _client.status.listen((status) {
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
        _emit(_state.copyWith(clearError: true));
      case TranscriptMessage():
        _applyTranscript(msg);
      case AudioStartEvent():
        // Assistant begins speaking — mute the mic until playback drains.
        _beginTts();
        _emit(_state.copyWith(liveState: LiveState.speaking));
      case AudioEndEvent():
        // Server finished sending audio, but the player is still draining its
        // buffer — keep the mic muted for that remaining playback + a margin.
        _endTtsAfterPlayback();
        if (_state.liveState == LiveState.speaking) {
          _emit(_state.copyWith(liveState: LiveState.idle));
        }
      case ToolCallMessage():
        _applyToolCall(msg);
      case ToolResultMessage():
        _applyToolResult(msg);
      case StateMessage():
        _emit(_state.copyWith(liveState: msg.value));
      case ErrorMessage():
        _log.warn('server error ${msg.code}: ${msg.message}');
        _emit(_state.copyWith(lastError: msg.message));
      case PongMessage():
        break; // handled inside the client (heartbeat)
      case ResolveContactRequestMessage():
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

  /// Cap on retained tool-activity rows, for the same reason as transcripts.
  static const int _maxTools = 40;

  void _applyToolCall(ToolCallMessage msg) {
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
      case 'rotate_camera':
        unawaited(setCameraPortrait(!_state.cameraPortrait));
      case 'end_session':
        // Let the spoken confirmation play out, then disconnect.
        Future<void>.delayed(
          const Duration(seconds: 3),
          () => unawaited(disconnect()),
        );
    }
  }

  void _applyToolResult(ToolResultMessage msg) {
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
    _applyOpenUrl(msg);
    _applyOpenMessaging(msg);

    // Voice flow: surface identify_image results so the UI can show the same
    // result sheet the scan button shows. The tool returns the full
    // {ok, mode, result} envelope as its payload.
    if (msg.name == 'identify_image' && msg.result != null) {
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
            unawaited(Notifications.schedule(id: id, body: title, when: when));
          }
        }
      case 'complete_task':
      case 'delete_task':
        unawaited(Notifications.cancel(id));
    }
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
        candidates.add({
          'contactId': id,
          'displayName': c.displayName,
          'maskedNumber': _maskPhone(clean),
        });
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
    final hits = all
        .where((c) => c.displayName.toLowerCase().contains(q))
        .toList()
      ..sort((a, b) {
        final ax = a.displayName.toLowerCase() == q ? 0 : 1;
        final bx = b.displayName.toLowerCase() == q ? 0 : 1;
        return ax.compareTo(bx);
      });
    return hits;
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
      await interrupt();
    }

    // Open the activity window BEFORE audio flows so the backend's manual VAD
    // counts the very first words (audio sent before audio_start is dropped).
    _client.send(const AudioStartMessage());
    await _startAudio();
    _emit(_state.copyWith(micOpen: true, liveState: LiveState.listening));
  }

  /// Close the mic and announce `audio_stop`.
  Future<void> stopListening() async {
    if (!_state.micOpen) return;
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
    _emit(_state.copyWith(transcripts: list));
  }

  /// Respond to a tool-permission gate.
  void respondToolPermission(String id, bool granted) {
    _client.send(ToolPermissionMessage(id: id, granted: granted));
  }

  // ---- Capture stream piping --------------------------------------------

  Future<void> _startAudio() async {
    await _activeSource.startAudio();
    await _audioSub?.cancel();
    _audioSub = _activeSource.audio16k.listen((pcm) {
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
    await _activeSource.stopAudio();
  }

  Future<void> _startVideo() async {
    await _activeSource.startVideo();
    await _videoSub?.cancel();
    _videoSub = _activeSource.jpegFrames.listen((jpeg) {
      // Always keep the freshest frame for the scan button / identify_image,
      // even while the assistant is speaking.
      _lastFrame = jpeg;
      // Don't feed frames while the assistant is speaking: they can't influence
      // the in-flight reply and would only pile up in the realtime model's
      // context, slowing later turns. Frames resume the moment TTS drains.
      if (_ttsActive) return;
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
    await _activeSource.stopVideo();
    _emit(_state.copyWith(cameraOn: false));
  }

  /// Enable/disable the camera stream at runtime.
  Future<void> setCameraEnabled(bool enabled) async {
    if (enabled == _state.cameraOn) return;
    if (enabled) {
      await _startVideo();
    } else {
      await _stopVideo();
    }
  }

  // ---- App lifecycle (background/foreground) -----------------------------

  bool _cameraWasOn = false;

  /// App went to the background: the OS invalidates the camera, so fully
  /// release it (a frozen dead controller is exactly the "camera stuck" hang).
  Future<void> handleAppBackground() async {
    _cameraWasOn = _state.cameraOn;
    if (_state.cameraOn) {
      await _stopVideo();
      await _activeSource.releaseCamera();
      _emit(_state.copyWith(cameraOn: false));
    }
  }

  /// App returned to the foreground: re-open the camera fresh if it was on and
  /// the session is still connected.
  Future<void> handleAppForeground() async {
    if (_cameraWasOn &&
        _state.connection == ConnectionStatus.connected &&
        !_state.cameraOn) {
      await _startVideo(); // re-opens a fresh controller (camera was released)
    }
  }

  /// Switch the camera preview/capture between portrait and landscape.
  Future<void> setCameraPortrait(bool portrait) async {
    if (portrait == _state.cameraPortrait) return;
    await _activeSource.setPortrait(portrait);
    _emit(_state.copyWith(cameraPortrait: portrait));
  }

  /// Zoom the camera and reflect the applied level in state (drives the UI
  /// read-out and presets). Used by pinch, the preset chips, and the model's
  /// `set_camera_zoom` tool.
  Future<void> setCameraZoom(double level) async {
    final applied = await _activeSource.setZoom(level);
    _emit(_state.copyWith(cameraZoom: applied));
  }

  // ---- Device switching (universal adapter) -----------------------------

  /// Switch the active capture device (phone ⇄ glasses). Stops streams on the
  /// old source, re-initializes the new one, and resumes video. The socket
  /// stays up; only the media origin changes, and the next `hello` (on any
  /// reconnect) will advertise the new device.
  Future<void> switchDevice(CaptureDeviceKind kind) async {
    if (kind == _registry.activeKind) return;
    _log.info('switching device → $kind');
    final wasListening = _state.micOpen;
    await _stopAudio();
    await _stopVideo();

    _registry.switchTo(kind);
    await _activeSource.initialize();

    await _startVideo();
    if (wasListening) await _startAudio();
    _emit(_state.copyWith(deviceKind: kind.name));
  }

  // ---- Config / reconnect target ----------------------------------------

  /// Point the client at a new backend (settings change) and reconnect.
  void updateConfig(AppConfig config) {
    _config = config;
    _client.updateConfig(config);
  }

  AppConfig get config => _config;

  /// Clear a transient error banner.
  void dismissError() => _emit(_state.copyWith(clearError: true));

  // ---- Disposal ----------------------------------------------------------

  Future<void> dispose() async {
    await ChatHistoryStore.saveSession(_state.transcripts);
    _ttsClear?.cancel();
    await _audioSub?.cancel();
    await _videoSub?.cancel();
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
