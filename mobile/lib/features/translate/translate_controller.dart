import 'dart:async';
import 'dart:typed_data';

import '../../capture/capture_source.dart';
import '../../capture/device_registry.dart';
import '../../core/config.dart';
import '../../core/logger.dart';
import '../../data/live_client.dart';
import '../../playback/pcm_player.dart';
import '../../protocol/frames.dart';
import '../../protocol/messages.dart';
import '../../protocol/protocol.dart';
import '../../state/permissions.dart';
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
/// Feedback is handled by the platform, not by gating: the capture source
/// already records through Android's voice-communication path with hardware
/// echo cancellation, which is what makes full duplex viable at all. There is
/// also an accidental second line of defence — with `echoTargetLanguage: false`
/// any translated audio that does leak back in is *already in the target
/// language*, so the model stays silent on it rather than translating its own
/// output.
///
/// Framework-agnostic (no Riverpod import) so it can be unit-tested directly,
/// matching `LiveController`.
class TranslateController {
  TranslateController({
    required AppConfig config,
    required DeviceRegistry registry,
    required PcmPlayer player,
    required PermissionsService permissions,
    WebSocketLiveClient Function(AppConfig, TranslateSessionConfig, DeviceInfo Function())?
        clientFactory,
  })  : _config = config,
        _registry = registry,
        _player = player,
        _permissions = permissions,
        _clientFactory = clientFactory ?? _defaultClientFactory;

  static final _log = Logger('TranslateController');

  /// Oldest turns are dropped past this. A translate session can run for an
  /// hour; an unbounded list would make every append O(n) and eventually
  /// exhaust memory. The transcript on screen is a live view, not the record.
  static const int maxTurns = 200;

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
  CaptureSource? _audioSource;
  bool _disposed = false;

  void _emit(TranslateState next) {
    _state = next;
    if (!_stateController.isClosed) _stateController.add(next);
  }

  /// Seed the target language / captions setting from config.
  void primeFromConfig(AppConfig cfg) {
    _config = cfg;
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

  /// Begin translating. Returns false when the microphone was refused.
  Future<bool> start() async {
    if (_disposed || _state.isRunning) return true;
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
      clearError: true,
    ));
    await _player.initialize();
    await _openSocket();
    return true;
  }

  /// Stop translating and release the microphone.
  Future<void> stop() async {
    if (!_state.isRunning && _client == null) return;
    await _stopAudio();
    await _teardownSocket();
    await _player.flush();
    await _player.stop();
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
    await source.initialize();
    // No mic gate and no echo guard, on purpose — see the class doc. Every
    // chunk goes up: a gate tuned to hold back non-speech would clip the
    // beginning of a sentence from across a room, and the whole point here is
    // to hear the room rather than the person holding the phone.
    _audioSub = source.audio16k.listen((chunk) {
      _client?.sendAudio(chunk);
    });
    await source.startAudio();
    _client?.send(const AudioStartMessage());
  }

  Future<void> _stopAudio() async {
    await _audioSub?.cancel();
    _audioSub = null;
    final source = _audioSource;
    _audioSource = null;
    if (source != null) {
      await source.stopAudio();
    }
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
      case ErrorMessage(:final message):
        _emit(_state.copyWith(error: message));
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
    await _stopAudio();
    await _teardownSocket();
    await _stateController.close();
  }
}
