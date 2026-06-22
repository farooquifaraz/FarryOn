/// FarryOn realtime wire-protocol constants.
///
/// This file is the Dart-side mirror of the shared contract in `PROTOCOL.md`.
/// Anything that travels over `/ws/live` is defined (or referenced) here so the
/// rest of the app never hard-codes magic numbers or string literals.
///
/// Keep this in lock-step with `PROTOCOL.md` and the Python backend.
library;

/// Wire-protocol version. Sent in the `hello` message and checked against the
/// server's `ready.protocolVersion`.
const int kProtocolVersion = 1;

/// Binary media-frame tags (the first byte of every binary frame).
///
/// See `PROTOCOL.md` §2. The values are part of the wire contract and MUST NOT
/// change without a protocol-version bump.
abstract final class FrameTag {
  /// client → server: PCM signed-16 LE, 16 kHz, mono.
  static const int inputAudio = 0x01;

  /// client → server: JPEG single frame (~1 fps, downscaled ≤ 1024 px).
  static const int inputVideo = 0x02;

  /// server → client: PCM signed-16 LE, 24 kHz, mono (streamed TTS).
  static const int outputAudio = 0x03;
}

/// Audio format constants. The mic captures at [micSampleRate]; the assistant's
/// TTS is streamed back at [ttsSampleRate] (see `PROTOCOL.md` §8).
abstract final class AudioFormat {
  static const String encoding = 'pcm16';
  static const int channels = 1;

  /// Microphone capture rate (INPUT_AUDIO, 0x01).
  static const int micSampleRate = 16000;

  /// TTS playback rate (OUTPUT_AUDIO, 0x03).
  static const int ttsSampleRate = 24000;

  /// Bytes per PCM16 sample (one channel).
  static const int bytesPerSample = 2;
}

/// Video format constants (INPUT_VIDEO, 0x02).
abstract final class VideoFormat {
  static const String format = 'jpeg';
  static const int fps = 1;
  // Larger frames give the vision model more detail (helps with distant or
  // zoomed-in subjects) at a modest bandwidth cost.
  static const int maxWidth = 1280;
}

/// `type` discriminator values for JSON control/event messages.
///
/// Grouped by direction purely for readability; they share one namespace on the
/// wire. See `PROTOCOL.md` §3 and §4.
abstract final class MsgType {
  // client → server
  static const String hello = 'hello';
  static const String config = 'config';
  static const String audioStart = 'audio_start';
  static const String audioStop = 'audio_stop';
  static const String text = 'text';
  static const String interrupt = 'interrupt';
  static const String toolPermission = 'tool_permission';
  static const String locationUpdate = 'location_update';
  static const String ping = 'ping';

  // server → client
  static const String ready = 'ready';
  static const String transcript = 'transcript';
  // audio_start is reused server → client (assistant begins speaking).
  static const String audioEnd = 'audio_end';
  static const String toolCall = 'tool_call';
  static const String toolResult = 'tool_result';
  static const String state = 'state';
  static const String error = 'error';
  static const String pong = 'pong';
}

/// Canonical tool names (see `PROTOCOL.md` §5). The UI renders tool activity by
/// matching on these; unknown names fall back to a generic renderer.
abstract final class ToolName {
  static const String createNote = 'create_note';
  static const String webSearch = 'web_search';
  static const String createTask = 'create_task';
  static const String sendMessage = 'send_message';
}

/// High-level conversational state reported by the server's `state` event and
/// surfaced in the UI (see `PROTOCOL.md` §4).
enum LiveState {
  idle,
  listening,
  thinking,
  speaking;

  /// Parse a wire string into a [LiveState], defaulting to [idle] for unknown
  /// values so a future server state never crashes the client.
  static LiveState fromWire(String? value) {
    switch (value) {
      case 'listening':
        return LiveState.listening;
      case 'thinking':
        return LiveState.thinking;
      case 'speaking':
        return LiveState.speaking;
      case 'idle':
      default:
        return LiveState.idle;
    }
  }

  /// The wire string for this state.
  String get wire => name;
}
