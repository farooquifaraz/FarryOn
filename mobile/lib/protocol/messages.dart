/// Typed models for every JSON control/event message on `/ws/live`.
///
/// These mirror `PROTOCOL.md` §3 (client → server) and §4 (server → client).
/// They are hand-written (rather than `freezed`/`json_serializable`) to keep the
/// protocol layer dependency-free and trivially unit-testable in pure Dart —
/// see `test/messages_test.dart`.
///
/// Every type exposes:
///   * a const constructor,
///   * `toJson()` producing the exact wire shape, and
///   * (for server messages) a `fromJson` factory.
library;

import 'protocol.dart';

// ---------------------------------------------------------------------------
// Client → Server
// ---------------------------------------------------------------------------

/// Base type for messages the client sends. All carry a `type` discriminator.
sealed class ClientMessage {
  const ClientMessage();

  /// The wire `type` value (see [MsgType]).
  String get type;

  /// Serialize to a JSON-encodable map matching `PROTOCOL.md`.
  Map<String, dynamic> toJson();
}

/// Identifies the client and the active capture device. Sent once right after
/// the socket opens (and again on reconnect, with [resumeId] populated).
class HelloMessage extends ClientMessage {
  const HelloMessage({
    required this.platform,
    required this.appVersion,
    required this.device,
    this.protocolVersion = kProtocolVersion,
    this.resumeId,
    this.provider,
    this.webSearch,
    this.email,
    this.clientTime,
  });

  /// `"android"` or `"ios"`.
  final String platform;
  final String appVersion;
  final DeviceInfo device;
  final int protocolVersion;

  /// Previous session id, used to resume context after a drop.
  final String? resumeId;

  /// Requested AI provider for this session (`gemini`/`openai`/`grok`/`mock`).
  /// Omitted when null so the backend uses its configured default.
  final String? provider;

  /// Per-session web-search config (`{provider, apiKey, fallbackProvider,
  /// fallbackApiKey}`); omitted when null so the backend uses its env settings.
  final Map<String, dynamic>? webSearch;

  /// Per-session email (IMAP) config (`{address, appPassword}`); omitted when
  /// not configured so the backend's `read_emails` tool stays disabled.
  final Map<String, dynamic>? email;

  /// The device's current local date-time as ISO-8601 with offset (e.g.
  /// `2026-06-21T22:30:00+05:30`). Lets the model resolve relative reminder
  /// times ("tomorrow at 5pm") in the user's timezone.
  final String? clientTime;

  @override
  String get type => MsgType.hello;

  @override
  Map<String, dynamic> toJson() => {
        'type': type,
        'protocolVersion': protocolVersion,
        'client': {'platform': platform, 'appVersion': appVersion},
        'device': device.toJson(),
        'session': {'resumeId': resumeId},
        if (provider != null) 'provider': provider,
        if (webSearch != null) 'webSearch': webSearch,
        if (email != null) 'email': email,
        if (clientTime != null) 'clientTime': clientTime,
      };
}

/// Describes the capture device feeding media (see `hello.device`).
class DeviceInfo {
  const DeviceInfo({
    required this.kind,
    required this.id,
    required this.capabilities,
  });

  /// `"phone" | "glasses" | "external"`.
  final String kind;
  final String id;

  /// e.g. `["audio_in", "video_in", "audio_out"]`.
  final List<String> capabilities;

  Map<String, dynamic> toJson() => {
        'kind': kind,
        'id': id,
        'capabilities': capabilities,
      };

  factory DeviceInfo.fromJson(Map<String, dynamic> json) => DeviceInfo(
        kind: json['kind'] as String? ?? 'phone',
        id: json['id'] as String? ?? '',
        capabilities: (json['capabilities'] as List<dynamic>? ?? const [])
            .map((e) => e as String)
            .toList(growable: false),
      );
}

/// Declares the media formats the client will send / expects to receive.
///
/// Defaults match the FarryOn contract: PCM16 mono 16 kHz in, JPEG 1 fps video,
/// PCM16 mono 24 kHz out.
class ConfigMessage extends ClientMessage {
  const ConfigMessage();

  @override
  String get type => MsgType.config;

  @override
  Map<String, dynamic> toJson() => {
        'type': type,
        'audioIn': {
          'encoding': AudioFormat.encoding,
          'sampleRate': AudioFormat.micSampleRate,
          'channels': AudioFormat.channels,
        },
        'videoIn': {
          'format': VideoFormat.format,
          'fps': VideoFormat.fps,
          'maxWidth': VideoFormat.maxWidth,
        },
        'audioOut': {
          'encoding': AudioFormat.encoding,
          'sampleRate': AudioFormat.ttsSampleRate,
          'channels': AudioFormat.channels,
        },
      };
}

/// User began speaking / mic opened.
class AudioStartMessage extends ClientMessage {
  const AudioStartMessage();
  @override
  String get type => MsgType.audioStart;
  @override
  Map<String, dynamic> toJson() => {'type': type};
}

/// Mic closed.
class AudioStopMessage extends ClientMessage {
  const AudioStopMessage();
  @override
  String get type => MsgType.audioStop;
  @override
  Map<String, dynamic> toJson() => {'type': type};
}

/// Typed user input (no mic).
class TextMessage extends ClientMessage {
  const TextMessage(this.text);
  final String text;
  @override
  String get type => MsgType.text;
  @override
  Map<String, dynamic> toJson() => {'type': type, 'text': text};
}

/// Pushes a fresh device location (`{lat, lng, address?}`) so the backend's
/// `get_location` tool can answer "where am I?".
class LocationUpdateMessage extends ClientMessage {
  const LocationUpdateMessage(this.location);
  final Map<String, dynamic> location;
  @override
  String get type => MsgType.locationUpdate;
  @override
  Map<String, dynamic> toJson() => {'type': type, 'location': location};
}

/// Barge-in: stop the current TTS playback.
class InterruptMessage extends ClientMessage {
  const InterruptMessage();
  @override
  String get type => MsgType.interrupt;
  @override
  Map<String, dynamic> toJson() => {'type': type};
}

/// Optional permission gate response for a tool call.
class ToolPermissionMessage extends ClientMessage {
  const ToolPermissionMessage({required this.id, required this.granted});
  final String id;
  final bool granted;
  @override
  String get type => MsgType.toolPermission;
  @override
  Map<String, dynamic> toJson() =>
      {'type': type, 'id': id, 'granted': granted};
}

/// Heartbeat ping. [t] is ms-since-epoch and is echoed back in `pong`.
class PingMessage extends ClientMessage {
  const PingMessage(this.t);
  final int t;
  @override
  String get type => MsgType.ping;
  @override
  Map<String, dynamic> toJson() => {'type': type, 't': t};
}

/// Reply to a [ResolveContactRequestMessage]: the result of matching a name
/// against the device's own contacts. Carries only MASKED numbers + opaque
/// contact ids — the real number never leaves the phone.
class ResolveContactResultMessage extends ClientMessage {
  const ResolveContactResultMessage({
    required this.requestId,
    required this.status,
    this.candidates = const [],
  });

  /// Correlates with the request's `requestId`.
  final String requestId;

  /// found | ambiguous | not_found | no_number | permission_denied.
  final String status;

  /// `[{contactId, displayName, maskedNumber}]` — empty unless found/ambiguous.
  final List<Map<String, dynamic>> candidates;

  @override
  String get type => MsgType.resolveContactResult;
  @override
  Map<String, dynamic> toJson() => {
        'type': type,
        'requestId': requestId,
        'status': status,
        'candidates': candidates,
      };
}

// ---------------------------------------------------------------------------
// Server → Client
// ---------------------------------------------------------------------------

/// Base type for messages the server sends.
sealed class ServerMessage {
  const ServerMessage();

  /// Parse a decoded JSON map into the matching [ServerMessage] subtype.
  ///
  /// Unknown `type` values yield an [UnknownServerMessage] rather than throwing,
  /// so a newer backend can add events without breaking older clients.
  factory ServerMessage.fromJson(Map<String, dynamic> json) {
    final type = json['type'] as String?;
    switch (type) {
      case MsgType.ready:
        return ReadyMessage.fromJson(json);
      case MsgType.transcript:
        return TranscriptMessage.fromJson(json);
      case MsgType.audioStart:
        return const AudioStartEvent();
      case MsgType.audioEnd:
        return const AudioEndEvent();
      case MsgType.toolCall:
        return ToolCallMessage.fromJson(json);
      case MsgType.toolResult:
        return ToolResultMessage.fromJson(json);
      case MsgType.state:
        return StateMessage.fromJson(json);
      case MsgType.error:
        return ErrorMessage.fromJson(json);
      case MsgType.pong:
        return PongMessage.fromJson(json);
      case MsgType.resolveContactRequest:
        return ResolveContactRequestMessage.fromJson(json);
      case MsgType.openMessaging:
        return OpenMessagingMessage.fromJson(json);
      default:
        return UnknownServerMessage(type ?? '<missing>', json);
    }
  }
}

/// Handshake acknowledgement; the session is live once this arrives.
class ReadyMessage extends ServerMessage {
  const ReadyMessage({
    required this.sessionId,
    required this.protocolVersion,
    this.model,
  });

  final String sessionId;
  final int protocolVersion;

  /// e.g. `"gemini-live" | "gpt-realtime"`.
  final String? model;

  factory ReadyMessage.fromJson(Map<String, dynamic> json) => ReadyMessage(
        sessionId: json['sessionId'] as String? ?? '',
        protocolVersion:
            (json['protocolVersion'] as num?)?.toInt() ?? kProtocolVersion,
        model: json['model'] as String?,
      );
}

/// Streaming transcript fragment for either side of the conversation.
class TranscriptMessage extends ServerMessage {
  const TranscriptMessage({
    required this.role,
    required this.text,
    required this.isFinal,
  });

  /// `"user"` (ASR) or `"assistant"`.
  final String role;
  final String text;

  /// Whether this is the final text for the current utterance.
  final bool isFinal;

  bool get isUser => role == 'user';
  bool get isAssistant => role == 'assistant';

  factory TranscriptMessage.fromJson(Map<String, dynamic> json) =>
      TranscriptMessage(
        role: json['role'] as String? ?? 'assistant',
        text: json['text'] as String? ?? '',
        isFinal: json['final'] as bool? ?? false,
      );
}

/// Assistant is about to send OUTPUT_AUDIO frames (begin speaking).
class AudioStartEvent extends ServerMessage {
  const AudioStartEvent();
}

/// Assistant finished sending OUTPUT_AUDIO frames.
class AudioEndEvent extends ServerMessage {
  const AudioEndEvent();
}

/// The model invoked a tool. Surfaced for UI display and optional gating.
class ToolCallMessage extends ServerMessage {
  const ToolCallMessage({
    required this.id,
    required this.name,
    required this.args,
    required this.needsPermission,
  });

  final String id;
  final String name;
  final Map<String, dynamic> args;
  final bool needsPermission;

  factory ToolCallMessage.fromJson(Map<String, dynamic> json) =>
      ToolCallMessage(
        id: json['id'] as String? ?? '',
        name: json['name'] as String? ?? '',
        args: (json['args'] as Map?)?.cast<String, dynamic>() ?? const {},
        needsPermission: json['needsPermission'] as bool? ?? false,
      );
}

/// Result of a previously-announced tool call.
class ToolResultMessage extends ServerMessage {
  const ToolResultMessage({
    required this.id,
    required this.name,
    required this.ok,
    this.result,
    this.error,
  });

  final String id;
  final String name;
  final bool ok;

  /// Tool-specific success payload (shape depends on [name]).
  final Map<String, dynamic>? result;

  /// Human-readable error when [ok] is false (best-effort; may be absent).
  final String? error;

  factory ToolResultMessage.fromJson(Map<String, dynamic> json) =>
      ToolResultMessage(
        id: json['id'] as String? ?? '',
        name: json['name'] as String? ?? '',
        ok: json['ok'] as bool? ?? false,
        result: (json['result'] as Map?)?.cast<String, dynamic>(),
        error: json['error'] as String?,
      );
}

/// High-level conversational state change.
class StateMessage extends ServerMessage {
  const StateMessage(this.value);
  final LiveState value;

  factory StateMessage.fromJson(Map<String, dynamic> json) =>
      StateMessage(LiveState.fromWire(json['value'] as String?));
}

/// Server-reported error. [fatal] indicates the session cannot continue.
class ErrorMessage extends ServerMessage {
  const ErrorMessage({
    required this.code,
    required this.message,
    required this.fatal,
  });

  final String code;
  final String message;
  final bool fatal;

  factory ErrorMessage.fromJson(Map<String, dynamic> json) => ErrorMessage(
        code: json['code'] as String? ?? 'unknown',
        message: json['message'] as String? ?? '',
        fatal: json['fatal'] as bool? ?? false,
      );
}

/// Heartbeat reply echoing the client's ping timestamp [t].
class PongMessage extends ServerMessage {
  const PongMessage(this.t);
  final int t;

  factory PongMessage.fromJson(Map<String, dynamic> json) =>
      PongMessage((json['t'] as num?)?.toInt() ?? 0);
}

/// The server asks the phone to resolve a contact NAME against its own
/// contacts (privacy-preserving). The phone replies with a
/// [ResolveContactResultMessage]. Never auto-sends — only looks up.
class ResolveContactRequestMessage extends ServerMessage {
  const ResolveContactRequestMessage({
    required this.requestId,
    required this.name,
    required this.channel,
  });

  final String requestId;
  final String name;

  /// whatsapp | sms | telegram.
  final String channel;

  factory ResolveContactRequestMessage.fromJson(Map<String, dynamic> json) =>
      ResolveContactRequestMessage(
        requestId: json['requestId'] as String? ?? '',
        name: json['name'] as String? ?? '',
        channel: json['channel'] as String? ?? 'whatsapp',
      );
}

/// The server tells the phone to open a messaging app for a previously-resolved
/// [contactId]. The phone looks up the real number locally (kept in a session
/// map) and opens WhatsApp / SMS with the message pre-filled.
class OpenMessagingMessage extends ServerMessage {
  const OpenMessagingMessage({
    required this.channel,
    required this.contactId,
    required this.message,
  });

  /// whatsapp | sms.
  final String channel;
  final String contactId;
  final String message;

  factory OpenMessagingMessage.fromJson(Map<String, dynamic> json) =>
      OpenMessagingMessage(
        channel: json['channel'] as String? ?? 'whatsapp',
        contactId: json['contact_id'] as String? ?? '',
        message: json['message'] as String? ?? '',
      );
}

/// Fallback for an unrecognized server `type`. Carries the raw map so callers
/// can log or inspect it without crashing.
class UnknownServerMessage extends ServerMessage {
  const UnknownServerMessage(this.type, this.raw);
  final String type;
  final Map<String, dynamic> raw;
}
