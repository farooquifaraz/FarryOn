import 'dart:typed_data';

import '../data/live_client.dart';
import '../protocol/protocol.dart';

/// One line of conversation transcript shown in the UI.
class TranscriptEntry {
  const TranscriptEntry({
    required this.role,
    required this.text,
    required this.isFinal,
  });

  /// `"user"` or `"assistant"`.
  final String role;
  final String text;
  final bool isFinal;

  bool get isUser => role == 'user';

  TranscriptEntry copyWith({String? text, bool? isFinal}) => TranscriptEntry(
        role: role,
        text: text ?? this.text,
        isFinal: isFinal ?? this.isFinal,
      );
}

/// A tool invocation and (eventually) its result, for the activity view.
class ToolActivity {
  const ToolActivity({
    required this.id,
    required this.name,
    required this.args,
    this.ok,
    this.result,
    this.error,
    this.needsPermission = false,
  });

  final String id;
  final String name;
  final Map<String, dynamic> args;

  /// Null while pending; true/false once a `tool_result` arrives.
  final bool? ok;
  final Map<String, dynamic>? result;
  final String? error;
  final bool needsPermission;

  bool get isPending => ok == null;

  ToolActivity copyWith({
    bool? ok,
    Map<String, dynamic>? result,
    String? error,
  }) =>
      ToolActivity(
        id: id,
        name: name,
        args: args,
        ok: ok ?? this.ok,
        result: result ?? this.result,
        error: error ?? this.error,
        needsPermission: needsPermission,
      );
}

/// Immutable snapshot of everything the live UI renders.
class LiveSessionState {
  const LiveSessionState({
    this.connection = ConnectionStatus.disconnected,
    this.liveState = LiveState.idle,
    this.micOpen = false,
    this.cameraOn = false,
    this.cameraPortrait = true,
    this.cameraFront = false,
    this.cameraZoom = 1.0,
    this.transcripts = const [],
    this.tools = const [],
    this.audioKind = 'phone',
    this.videoKind = 'phone',
    this.glassesConnected = false,
    this.glassesBattery,
    this.glassesTalking = false,
    this.glassesWorn = false,
    this.lastCapturedPhoto,
    this.lastCapturedAt,
    this.lastError,
    this.permissionsGranted = false,
  });

  /// Socket-level status.
  final ConnectionStatus connection;

  /// Conversational state reported by the server (`state` events).
  final LiveState liveState;

  /// Whether the mic is currently streaming.
  final bool micOpen;

  /// Whether the camera is currently streaming frames.
  final bool cameraOn;

  /// Whether the camera preview is in portrait (`true`) or landscape.
  final bool cameraPortrait;

  /// Whether the phone's FRONT (selfie) lens is active (`false` = back lens).
  /// Only meaningful for the phone camera; glasses have a single fixed lens.
  final bool cameraFront;

  /// Current camera zoom magnification (1.0 = normal). Driven by pinch, the
  /// preset chips, or the model's `set_camera_zoom` tool.
  final double cameraZoom;

  /// Ordered transcript lines (oldest first).
  final List<TranscriptEntry> transcripts;

  /// Tool activity, most-recent last.
  final List<ToolActivity> tools;

  /// Device supplying the microphone (for the UI badge).
  final String audioKind;

  /// Device supplying the camera (for the UI badge).
  final String videoKind;

  /// Combined label for the status badge: one name when both channels share a
  /// device, otherwise `audio+video`.
  String get deviceKind =>
      audioKind == videoKind ? audioKind : '$audioKind+$videoKind';

  /// Glasses link status (only meaningful when audioKind == 'glasses').
  final bool glassesConnected;
  final int? glassesBattery;

  /// True while the user is long-pressing and glasses-mic PCM is flowing.
  final bool glassesTalking;

  /// True while the glasses are being worn (wear-to-talk auto-listen).
  final bool glassesWorn;

  /// The most recent glasses photo (raw JPEG), shown as a preview in the chat
  /// so the user can visually confirm what was actually captured and sent for
  /// recognition. Null until the first glasses capture.
  final Uint8List? lastCapturedPhoto;

  /// When [lastCapturedPhoto] was captured, for the preview caption.
  final DateTime? lastCapturedAt;

  /// Last non-fatal error message for a transient banner, if any.
  final String? lastError;

  /// Whether mic+camera OS permissions have been granted.
  final bool permissionsGranted;

  bool get isConnected => connection == ConnectionStatus.connected;

  LiveSessionState copyWith({
    ConnectionStatus? connection,
    LiveState? liveState,
    bool? micOpen,
    bool? cameraOn,
    bool? cameraPortrait,
    bool? cameraFront,
    double? cameraZoom,
    List<TranscriptEntry>? transcripts,
    List<ToolActivity>? tools,
    String? audioKind,
    String? videoKind,
    bool? glassesConnected,
    int? glassesBattery,
    bool? glassesTalking,
    bool? glassesWorn,
    Uint8List? lastCapturedPhoto,
    DateTime? lastCapturedAt,
    String? lastError,
    bool clearError = false,
    bool? permissionsGranted,
  }) =>
      LiveSessionState(
        connection: connection ?? this.connection,
        liveState: liveState ?? this.liveState,
        micOpen: micOpen ?? this.micOpen,
        cameraOn: cameraOn ?? this.cameraOn,
        cameraPortrait: cameraPortrait ?? this.cameraPortrait,
        cameraFront: cameraFront ?? this.cameraFront,
        cameraZoom: cameraZoom ?? this.cameraZoom,
        transcripts: transcripts ?? this.transcripts,
        tools: tools ?? this.tools,
        audioKind: audioKind ?? this.audioKind,
        videoKind: videoKind ?? this.videoKind,
        glassesConnected: glassesConnected ?? this.glassesConnected,
        glassesBattery: glassesBattery ?? this.glassesBattery,
        glassesTalking: glassesTalking ?? this.glassesTalking,
        glassesWorn: glassesWorn ?? this.glassesWorn,
        lastCapturedPhoto: lastCapturedPhoto ?? this.lastCapturedPhoto,
        lastCapturedAt: lastCapturedAt ?? this.lastCapturedAt,
        lastError: clearError ? null : (lastError ?? this.lastError),
        permissionsGranted: permissionsGranted ?? this.permissionsGranted,
      );
}
