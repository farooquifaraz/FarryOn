import '../../protocol/protocol.dart';
import '../../state/live_state.dart';

/// What the mic chip says. One truthful word for what the microphone is
/// doing right now — not what was asked of it.
///
/// Before this the chip lit up on `micOpen` alone, and stayed lit through ten
/// minutes in which not one chunk reached the server (2026-09-08). The user
/// could not tell "listening" from "deaf"; now they can.
enum MicStatus {
  /// The user muted the mic.
  muted('Muted'),

  /// Mic open, quiet room — waiting for a voice.
  listening('Listening'),

  /// Speech is going out right now.
  hearing('Hearing you…'),

  /// The user's turn is over; the answer is being worked out.
  thinking('Thinking…'),

  /// Farry is talking; the mic is held shut against her own voice.
  speaking('Speaking'),

  /// The recorder went quiet and is being brought back.
  restarting('Restarting mic…'),

  /// Restarted and still silent — the user has been told.
  notHearing('Not hearing you');

  const MicStatus(this.label);

  final String label;

  /// True for the states the mic is genuinely able to hear in.
  bool get canHear => this == listening || this == hearing;

  /// True for the states that mean something is wrong with the mic.
  bool get isFault => this == restarting || this == notHearing;
}

/// Order matters: a fault outranks conversation state, and Farry's own voice
/// outranks the gate (her speech never counts as "hearing you").
MicStatus micStatusFor(LiveSessionState s) {
  if (!s.micOpen) return MicStatus.muted;
  switch (s.micHealth) {
    case MicHealth.silent:
      return MicStatus.notHearing;
    case MicHealth.restarting:
      return MicStatus.restarting;
    case MicHealth.ok:
      break;
  }
  if (s.liveState == LiveState.speaking) return MicStatus.speaking;
  if (s.hearing) return MicStatus.hearing;
  if (s.liveState == LiveState.thinking) return MicStatus.thinking;
  return MicStatus.listening;
}
