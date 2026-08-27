import 'dart:typed_data';

import '../data/live_client.dart';
import '../protocol/protocol.dart';

/// One line of conversation transcript shown in the UI.
class TranscriptEntry {
  TranscriptEntry({
    required this.role,
    required this.text,
    required this.isFinal,
    DateTime? time,
  }) : time = time ?? DateTime.now();

  /// `"user"`, `"assistant"`, or `"notice"`.
  ///
  /// A notice is the app correcting the record — "this reminder won't fire" —
  /// and must not be dressed as Farry: she said it *was* set, and putting the
  /// contradiction in her voice reads as her changing her mind rather than as
  /// the phone reporting a fact she can't see.
  final String role;
  final String text;
  final bool isFinal;

  /// When this line FIRST appeared: for a user line, the moment Farry began
  /// hearing it; for an assistant line, the moment the reply began. The gap
  /// between consecutive bubbles is therefore the felt latency — which is why
  /// the chat shows these times (user-asked 2026-08-27). Streaming updates
  /// keep the original time (see [copyWith]).
  final DateTime time;

  bool get isUser => role == 'user';
  bool get isNotice => role == 'notice';

  TranscriptEntry copyWith({String? text, bool? isFinal}) => TranscriptEntry(
        role: role,
        text: text ?? this.text,
        isFinal: isFinal ?? this.isFinal,
        time: time,
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
    this.glassesAudioReady,
    this.glassesAudioPaired,
    this.glassesName,
    this.glassesTalking = false,
    this.glassesWorn = false,
    this.recording,
    this.syncStatus,
    this.pendingMedia,
    this.recordingBusy = false,
    this.lastCapturedPhoto,
    this.lastCapturedAt,
    this.lastError,
    this.permissionsGranted = false,
    this.capReached = false,
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

  /// Whether the glasses' AUDIO link is up, and whether they are paired at all.
  /// [glassesConnected] is the BLE control link — battery, photos, mic — and it
  /// can be perfectly healthy while the voice still comes out of the phone.
  /// Null until the phone has answered.
  final bool? glassesAudioReady;
  final bool? glassesAudioPaired;

  /// Device name of the (last) connected glasses, e.g. "L802_2B1D" — shown on
  /// the dashboard card. Kept across a drop so the card can say WHICH pair.
  final String? glassesName;

  /// True while the user is long-pressing and glasses-mic PCM is flowing.
  final bool glassesTalking;

  /// True while the glasses are being worn (wear-to-talk auto-listen).
  final bool glassesWorn;

  /// The glasses video recording in progress, or null when nothing is
  /// recording. Purely additive: every other part of the session ignores it.
  final GlassesRecording? recording;

  /// True while a start/stop is in flight with the glasses. Start and stop are
  /// the same command, so a second tap before the device answers would undo
  /// the first — the button is held disabled until it does.
  final bool recordingBusy;

  /// What is waiting on the glasses, as they last reported it. Drives the
  /// dashboard's "waiting to sync" chip in manual mode — without it, manual
  /// sync is a setting the user forgets until the glasses fill up.
  final GlassesMedia? pendingMedia;

  /// A glasses media transfer in progress, or null when nothing is syncing.
  /// Shown on the dashboard so a long video transfer isn't invisible.
  final GlassesSync? syncStatus;

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

  /// The session ended because today's plan cap was reached (not a fault). Lets
  /// the reconnect overlay offer Upgrade instead of a Retry that re-hits the cap.
  /// Cleared when a new session starts.
  final bool capReached;

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
    bool? glassesAudioReady,
    bool? glassesAudioPaired,
    String? glassesName,
    bool? glassesTalking,
    bool? glassesWorn,
    GlassesRecording? recording,
    bool clearRecording = false,
    GlassesSync? syncStatus,
    bool clearSync = false,
    GlassesMedia? pendingMedia,
    bool? recordingBusy,
    Uint8List? lastCapturedPhoto,
    DateTime? lastCapturedAt,
    String? lastError,
    bool clearError = false,
    bool? permissionsGranted,
    bool? capReached,
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
        glassesAudioReady: glassesAudioReady ?? this.glassesAudioReady,
        glassesAudioPaired: glassesAudioPaired ?? this.glassesAudioPaired,
        glassesName: glassesName ?? this.glassesName,
        glassesTalking: glassesTalking ?? this.glassesTalking,
        glassesWorn: glassesWorn ?? this.glassesWorn,
        recording: clearRecording ? null : (recording ?? this.recording),
        syncStatus: clearSync ? null : (syncStatus ?? this.syncStatus),
        pendingMedia: pendingMedia ?? this.pendingMedia,
        recordingBusy: recordingBusy ?? this.recordingBusy,
        lastCapturedPhoto: lastCapturedPhoto ?? this.lastCapturedPhoto,
        lastCapturedAt: lastCapturedAt ?? this.lastCapturedAt,
        lastError: clearError ? null : (lastError ?? this.lastError),
        permissionsGranted: permissionsGranted ?? this.permissionsGranted,
        capReached: capReached ?? this.capReached,
      );
}

/// A glasses video recording in progress.
///
/// The clock the UI shows is derived from [startedAt] rather than a counter the
/// app increments, so a rebuild, a backgrounded app or a dropped frame can
/// never make it drift away from the real recording.
class GlassesRecording {
  const GlassesRecording({
    required this.requestId,
    required this.seconds,
    required this.startedAt,
  });

  /// Correlates with the `videoState` events from the native bridge.
  final String requestId;

  /// The length the user picked — what the glasses' firmware will stop at.
  final int seconds;

  final DateTime startedAt;

  Duration get elapsed => DateTime.now().difference(startedAt);

  /// Never past [seconds], so the label can't read "2:07 / 2:00" if a stop
  /// event is a moment late.
  Duration get clampedElapsed {
    final e = elapsed;
    final total = Duration(seconds: seconds);
    return e > total ? total : (e.isNegative ? Duration.zero : e);
  }

  static String format(Duration d) {
    final m = d.inMinutes;
    final s = d.inSeconds % 60;
    return '$m:${s.toString().padLeft(2, '0')}';
  }

  /// e.g. `0:42 / 2:00`.
  String get label =>
      '${format(clampedElapsed)} / ${format(Duration(seconds: seconds))}';
}

/// A glasses → phone media transfer in progress.
class GlassesSync {
  const GlassesSync({
    required this.file,
    required this.pct,
    this.speedKbps,
    this.index = 0,
    this.total = 0,
  });

  /// Which file of how many, 1-based. Zero when the glasses haven't said yet.
  /// With several clips queued a bare percentage restarts at 0 each time and
  /// reads exactly like a stall, so the count is what makes it legible.
  final int index;
  final int total;

  /// The file being transferred, or a stage label ("WiFi-P2P pairing…").
  final String file;

  /// 0-100. Reaching 100 ends the transfer.
  final int pct;

  final double? speedKbps;

  /// Short enough for a one-line status strip: "2 of 3 · 47% · 3.6 MB/s".
  String get label {
    final speed = speedKbps;
    final rate = (speed == null || speed <= 0)
        ? ''
        : ' · ${speed.toStringAsFixed(1)} MB/s';
    final of = total > 1 ? '$index of $total · ' : '';
    return '$of$pct%$rate';
  }

  /// How many are still to come after this one.
  int get remaining => total > index ? total - index : 0;
}

/// What the glasses are holding that hasn't reached the phone yet.
class GlassesMedia {
  const GlassesMedia({this.photos = 0, this.videos = 0, this.recordings = 0});

  final int photos;
  final int videos;
  final int recordings;

  int get total => photos + videos + recordings;
  bool get isEmpty => total == 0;

  /// "2 videos", "1 photo · 3 videos" — only the kinds that are actually there,
  /// so the user reads a fact rather than a form.
  String get label {
    final parts = <String>[
      if (photos > 0) '$photos photo${photos == 1 ? '' : 's'}',
      if (videos > 0) '$videos video${videos == 1 ? '' : 's'}',
      if (recordings > 0) '$recordings recording${recordings == 1 ? '' : 's'}',
    ];
    return parts.join(' · ');
  }
}
