import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';

import '../capture/capture_source.dart';
import '../capture/device_registry.dart';
import '../core/config.dart';
import '../core/logger.dart';
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
    this.platform = defaultPlatform,
  })  : _config = config,
        _registry = registry,
        _player = player,
        _permissions = permissions {
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

  // ---- Observable state --------------------------------------------------

  final _stateController =
      StreamController<LiveSessionState>.broadcast(sync: false);

  /// Stream of state snapshots for the UI.
  Stream<LiveSessionState> get stateStream => _stateController.stream;

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
    // begin flowing; the mic is gated behind the push-to-talk control.
    await _startVideo();

    _client.start();
    return outcome;
  }

  /// Tear down capture, playback, and the socket (keeps objects reusable).
  Future<void> disconnect() async {
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
        // Fire-and-forget; PcmPlayer applies its own backpressure.
        unawaited(_player.feed(frame.payload));
      }
    });

    _eventSub = _client.events.listen(_onServerMessage);
  }

  void _onServerMessage(ServerMessage msg) {
    switch (msg) {
      case ReadyMessage():
        _emit(_state.copyWith(clearError: true));
      case TranscriptMessage():
        _applyTranscript(msg);
      case AudioStartEvent():
        // Assistant begins speaking.
        _emit(_state.copyWith(liveState: LiveState.speaking));
      case AudioEndEvent():
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
      case UnknownServerMessage():
        _log.debug('unknown server message: ${msg.type}');
    }
  }

  void _applyTranscript(TranscriptMessage msg) {
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
    _emit(_state.copyWith(transcripts: list));
  }

  void _applyToolCall(ToolCallMessage msg) {
    final list = List<ToolActivity>.of(_state.tools)
      ..add(ToolActivity(
        id: msg.id,
        name: msg.name,
        args: msg.args,
        needsPermission: msg.needsPermission,
      ));
    _emit(_state.copyWith(tools: list));
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

    await _startAudio();
    _client.send(const AudioStartMessage());
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
      _client.sendVideo(jpeg);
    });
    _emit(_state.copyWith(cameraOn: true));
  }

  Future<void> _stopVideo() async {
    await _videoSub?.cancel();
    _videoSub = null;
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
    await _audioSub?.cancel();
    await _videoSub?.cancel();
    await _eventSub?.cancel();
    await _frameSub?.cancel();
    await _statusSub?.cancel();
    await _client.dispose();
    await _player.dispose();
    await _registry.dispose();
    await _stateController.close();
  }
}
