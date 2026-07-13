import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:web_socket_channel/status.dart' as ws_status;
import 'package:web_socket_channel/web_socket_channel.dart';

import '../core/config.dart';
import '../core/logger.dart';
import '../protocol/frames.dart';
import '../protocol/messages.dart';
import '../protocol/protocol.dart';

/// Connection lifecycle for the [WebSocketLiveClient], surfaced to the UI.
enum ConnectionStatus {
  /// Not connected and not trying.
  disconnected,

  /// Socket opening or waiting on the `ready` handshake.
  connecting,

  /// Waiting out a backoff delay before the next connect attempt.
  reconnecting,

  /// Socket open and `ready` received — fully usable.
  connected,
}

/// Factory for the underlying [WebSocketChannel]. Injectable so tests can supply
/// a fake transport without a real network.
typedef ChannelFactory = WebSocketChannel Function(Uri uri);

WebSocketChannel _defaultChannelFactory(Uri uri) =>
    WebSocketChannel.connect(uri);

/// Resilient client for the FarryOn `/ws/live` WebSocket.
///
/// Responsibilities (see `PROTOCOL.md` §6–§7):
///   * Open the socket and run the `hello` → `config` → `ready` handshake.
///   * Multiplex JSON control/event messages and binary media frames over one
///     socket.
///   * Emit decoded [ServerMessage]s on [events] and decoded binary
///     [DecodedFrame]s on [frames].
///   * Heartbeat: send `ping` every 15 s; if no `pong` within 10 s, drop and
///     reconnect.
///   * Reconnect with exponential backoff + jitter (0.5 → 8 s), reset on
///     `ready`, and resume context via `session.resumeId`.
///
/// The client owns no media capture or playback; the controller wires those in.
class WebSocketLiveClient {
  WebSocketLiveClient({
    required AppConfig config,
    required this.platform,
    required DeviceInfo Function() deviceInfoProvider,
    ChannelFactory channelFactory = _defaultChannelFactory,
  })  : _config = config,
        _deviceInfoProvider = deviceInfoProvider,
        _channelFactory = channelFactory;

  static final _log = Logger('LiveClient');

  AppConfig _config;
  final String platform;
  final DeviceInfo Function() _deviceInfoProvider;
  final ChannelFactory _channelFactory;

  // Heartbeat / reconnect tuning (from PROTOCOL.md §7).
  static const Duration _pingInterval = Duration(seconds: 15);
  static const Duration _pongTimeout = Duration(seconds: 10);
  static const Duration _baseBackoff = Duration(milliseconds: 500);
  static const Duration _maxBackoff = Duration(seconds: 8);

  final _events = StreamController<ServerMessage>.broadcast();
  final _frames = StreamController<DecodedFrame>.broadcast();
  final _status =
      StreamController<ConnectionStatus>.broadcast(sync: false);

  /// Decoded JSON control/event messages from the server.
  Stream<ServerMessage> get events => _events.stream;

  /// Decoded binary media frames (OUTPUT_AUDIO and any future binary streams).
  Stream<DecodedFrame> get frames => _frames.stream;

  /// Connection-status changes for the UI.
  Stream<ConnectionStatus> get status => _status.stream;

  ConnectionStatus _currentStatus = ConnectionStatus.disconnected;
  ConnectionStatus get currentStatus => _currentStatus;

  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _socketSub;
  Timer? _pingTimer;
  Timer? _pongTimer;
  Timer? _reconnectTimer;

  int _backoffAttempt = 0;
  final _random = Random();

  /// Last session id from a `ready`, replayed as `session.resumeId` on
  /// reconnect so the backend can re-attach context.
  String? _resumeId;
  String? get resumeId => _resumeId;

  bool _started = false; // user wants to be connected
  bool _disposed = false;

  /// Update the backend target. If currently started, this forces a clean
  /// reconnect against the new endpoint.
  void updateConfig(AppConfig config) {
    _config = config;
    if (_started && !_disposed) {
      _log.info('config updated → reconnecting to ${config.liveUri}');
      _teardownSocket();
      _connect();
    }
  }

  /// Begin connecting and keep the connection alive (auto-reconnect) until
  /// [stop] or [dispose] is called. Idempotent.
  void start() {
    if (_disposed || _started) return;
    _started = true;
    _backoffAttempt = 0;
    _connect();
  }

  /// Stop connecting and close any open socket. The client can be [start]ed
  /// again afterwards.
  Future<void> stop() async {
    _started = false;
    _reconnectTimer?.cancel();
    await _teardownSocket(closeCode: ws_status.normalClosure);
    _setStatus(ConnectionStatus.disconnected);
  }

  // ---- Sending -----------------------------------------------------------

  /// Send a typed client JSON message. No-op if the socket is not open.
  void send(ClientMessage message) => _sendRaw(message.toJson());

  void _sendRaw(Map<String, dynamic> json) {
    final sink = _channel?.sink;
    if (sink == null) {
      _log.debug('drop json (${json['type']}): socket not open');
      return;
    }
    sink.add(jsonEncode(json));
  }

  /// Send an INPUT_AUDIO (0x01) frame. Payload must be PCM16 LE mono 16 kHz.
  void sendAudio(Uint8List pcm16, {int? timestampMs}) =>
      _sendFrame(FrameTag.inputAudio, pcm16, timestampMs);

  /// Send an INPUT_VIDEO (0x02) frame. Payload must be a single JPEG image.
  void sendVideo(Uint8List jpeg, {int? timestampMs}) =>
      _sendFrame(FrameTag.inputVideo, jpeg, timestampMs);

  void _sendFrame(int tag, Uint8List payload, int? timestampMs) {
    final sink = _channel?.sink;
    if (sink == null) {
      // Per §7: media captured while disconnected is discarded, not buffered.
      return;
    }
    sink.add(MediaFrame.encode(
      tag: tag,
      timestampMs: timestampMs ?? DateTime.now().millisecondsSinceEpoch,
      payload: payload,
    ));
  }

  // ---- Connection management --------------------------------------------

  void _connect() {
    if (_disposed || !_started) return;
    _reconnectTimer?.cancel();
    _setStatus(ConnectionStatus.connecting);

    final uri = _config.liveUri;
    _log.info('connecting to $uri (attempt ${_backoffAttempt + 1})');

    final WebSocketChannel channel;
    try {
      channel = _channelFactory(uri);
    } catch (e, st) {
      _log.error('connect failed synchronously', e, st);
      _scheduleReconnect();
      return;
    }
    _channel = channel;

    _socketSub = channel.stream.listen(
      _onSocketData,
      onError: _onSocketError,
      onDone: _onSocketDone,
      cancelOnError: false,
    );

    // Handshake immediately; the server replies with `ready` (§6).
    _sendHandshake();
  }

  void _sendHandshake() {
    final device = _deviceInfoProvider();
    final wsKey = _config.webSearchApiKey;
    // Phase 1 wire: still a single `hello.email`, carrying the PRIMARY account.
    // (Phase 2 upgrades this to send every configured account as `hello.emails`.)
    final primaryEmail = _config.primaryEmailAccount;
    send(HelloMessage(
      platform: platform,
      appVersion: _config.appVersion,
      device: device,
      resumeId: _resumeId,
      provider: _config.provider,
      clientTime: _localTimeIso(),
      // Only send web-search config when the user supplied a primary key,
      // otherwise let the backend use its own env settings.
      webSearch: (wsKey != null && wsKey.isNotEmpty)
          ? {
              'provider': _config.webSearchProvider,
              'apiKey': wsKey,
              'fallbackProvider': _config.webSearchFallbackProvider,
              'fallbackApiKey': _config.webSearchFallbackApiKey ?? '',
            }
          : null,
      // Only send email config when the primary account is fully set, so the
      // backend's read_emails tool stays disabled until the user opts in.
      email: (primaryEmail != null && primaryEmail.isComplete)
          ? {
              'address': primaryEmail.address,
              'appPassword': primaryEmail.appPassword,
              if (primaryEmail.resolvedImapHost.isNotEmpty)
                'host': primaryEmail.resolvedImapHost,
              if (primaryEmail.resolvedSmtpHost.isNotEmpty)
                'smtpHost': primaryEmail.resolvedSmtpHost,
              'smtpPort': primaryEmail.resolvedSmtpPort,
            }
          : null,
    ));
    send(const ConfigMessage());
  }

  /// Local date-time as ISO-8601 with the device's UTC offset, e.g.
  /// `2026-06-21T22:30:00+05:30` — so the backend can resolve reminder times.
  static String _localTimeIso() {
    final now = DateTime.now();
    final off = now.timeZoneOffset;
    final sign = off.isNegative ? '-' : '+';
    final hh = off.inHours.abs().toString().padLeft(2, '0');
    final mm = (off.inMinutes.abs() % 60).toString().padLeft(2, '0');
    final base = now.toIso8601String();
    final noMillis = base.contains('.') ? base.split('.').first : base;
    return '$noMillis$sign$hh:$mm';
  }

  void _onSocketData(dynamic data) {
    if (data is String) {
      _handleJson(data);
    } else if (data is List<int>) {
      _handleBinary(data);
    } else {
      _log.warn('unexpected socket payload type: ${data.runtimeType}');
    }
  }

  void _handleJson(String text) {
    Map<String, dynamic> map;
    try {
      final decoded = jsonDecode(text);
      if (decoded is! Map<String, dynamic>) {
        _log.warn('non-object JSON frame ignored');
        return;
      }
      map = decoded;
    } catch (e) {
      _log.warn('bad JSON frame ignored: $e');
      return;
    }

    final msg = ServerMessage.fromJson(map);

    // Intercept lifecycle-relevant messages before forwarding.
    if (msg is ReadyMessage) {
      _onReady(msg);
    } else if (msg is PongMessage) {
      _onPong();
    }

    _events.add(msg);
  }

  void _handleBinary(List<int> bytes) {
    try {
      _frames.add(MediaFrame.decode(bytes));
    } catch (e) {
      _log.warn('bad binary frame ignored: $e');
    }
  }

  void _onReady(ReadyMessage msg) {
    _resumeId = msg.sessionId;
    _backoffAttempt = 0; // reset backoff on a successful handshake (§7)
    _setStatus(ConnectionStatus.connected);
    _startHeartbeat();
    if (msg.protocolVersion != kProtocolVersion) {
      _log.warn(
        'protocol mismatch: server=${msg.protocolVersion} '
        'client=$kProtocolVersion',
      );
    }
    _log.info('ready: session=${msg.sessionId} model=${msg.model}');
  }

  void _onSocketError(Object error, StackTrace st) {
    _log.warn('socket error: $error');
    _handleDrop();
  }

  void _onSocketDone() {
    _log.info('socket closed (code=${_channel?.closeCode})');
    _handleDrop();
  }

  void _handleDrop() {
    if (_disposed) return;
    _teardownSocket();
    if (_started) {
      _scheduleReconnect();
    } else {
      _setStatus(ConnectionStatus.disconnected);
    }
  }

  // ---- Heartbeat ---------------------------------------------------------

  void _startHeartbeat() {
    _pingTimer?.cancel();
    _pongTimer?.cancel();
    _pingTimer = Timer.periodic(_pingInterval, (_) => _sendPing());
    _sendPing(); // prime immediately so a dead link is caught fast
  }

  void _sendPing() {
    final now = DateTime.now().millisecondsSinceEpoch;
    send(PingMessage(now));
    // Arm (or re-arm) the pong watchdog: no pong in 10 s ⇒ drop + reconnect.
    _pongTimer?.cancel();
    _pongTimer = Timer(_pongTimeout, () {
      _log.warn('no pong within ${_pongTimeout.inSeconds}s → reconnecting');
      _handleDrop();
    });
  }

  void _onPong() {
    _pongTimer?.cancel();
    _pongTimer = null;
  }

  // ---- Reconnect with exponential backoff + jitter -----------------------

  void _scheduleReconnect() {
    if (_disposed || !_started) return;
    _setStatus(ConnectionStatus.reconnecting);
    final delay = _nextBackoff();
    _backoffAttempt++;
    _log.info('reconnecting in ${delay.inMilliseconds}ms');
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(delay, _connect);
  }

  /// Compute the next backoff delay: `base * 2^attempt` capped at [_maxBackoff],
  /// with full jitter in `[0, capped]` (PROTOCOL.md §7: 0.5, 1, 2, 4, 8 s).
  Duration _nextBackoff() {
    final exp = _baseBackoff.inMilliseconds * (1 << _backoffAttempt);
    final capped = min(exp, _maxBackoff.inMilliseconds);
    final jittered = _random.nextInt(capped + 1);
    return Duration(milliseconds: jittered);
  }

  // ---- Teardown ----------------------------------------------------------

  Future<void> _teardownSocket({int? closeCode}) async {
    _pingTimer?.cancel();
    _pongTimer?.cancel();
    _pingTimer = null;
    _pongTimer = null;

    final sub = _socketSub;
    final channel = _channel;
    _socketSub = null;
    _channel = null;

    await sub?.cancel();
    try {
      await channel?.sink.close(closeCode);
    } catch (_) {
      // Ignore close races on an already-dead socket.
    }
  }

  void _setStatus(ConnectionStatus status) {
    if (status == _currentStatus) return;
    _currentStatus = status;
    if (!_status.isClosed) _status.add(status);
  }

  /// Permanently release all resources. The client is unusable afterwards.
  Future<void> dispose() async {
    _disposed = true;
    _started = false;
    _reconnectTimer?.cancel();
    await _teardownSocket(closeCode: ws_status.goingAway);
    await _events.close();
    await _frames.close();
    await _status.close();
  }
}
