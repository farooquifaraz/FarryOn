// What the mic chip says, in priority order.
import 'package:farryon/features/live/mic_status.dart';
import 'package:farryon/protocol/protocol.dart';
import 'package:farryon/state/live_state.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  const open = LiveSessionState(micOpen: true);

  test('a closed mic is muted, whatever else is going on', () {
    expect(
      micStatusFor(const LiveSessionState(
        micOpen: false,
        hearing: true,
        liveState: LiveState.speaking,
        micHealth: MicHealth.silent,
      )),
      MicStatus.muted,
    );
  });

  test('a fault outranks the conversation', () {
    expect(
      micStatusFor(open.copyWith(
          micHealth: MicHealth.silent, liveState: LiveState.speaking)),
      MicStatus.notHearing,
    );
    expect(
      micStatusFor(open.copyWith(micHealth: MicHealth.restarting, hearing: true)),
      MicStatus.restarting,
    );
  });

  test("Farry's own voice is never 'hearing you'", () {
    expect(
      micStatusFor(open.copyWith(liveState: LiveState.speaking, hearing: true)),
      MicStatus.speaking,
    );
  });

  test('speech going out is hearing; a quiet open mic is listening', () {
    expect(micStatusFor(open.copyWith(hearing: true)), MicStatus.hearing);
    expect(micStatusFor(open), MicStatus.listening);
    expect(micStatusFor(open.copyWith(liveState: LiveState.thinking)),
        MicStatus.thinking);
  });

  test('only the two hearing states claim the mic can hear', () {
    for (final s in MicStatus.values) {
      expect(s.canHear, s == MicStatus.listening || s == MicStatus.hearing,
          reason: s.name);
      expect(s.isFault, s == MicStatus.restarting || s == MicStatus.notHearing,
          reason: s.name);
    }
  });
}
