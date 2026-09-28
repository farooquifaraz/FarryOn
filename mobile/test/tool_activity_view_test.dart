/// The "Looking" card: while a photo is taken and looked at, the user is
/// told how long that usually takes and sees the seconds tick, instead of a
/// bare "Running…" (live 2026-09-27: 30 s of silence per look, and nothing
/// on screen said whether anything was happening).
library;

import 'package:farryon/features/live/widgets/tool_activity_view.dart';
import 'package:farryon/state/live_state.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

final DateTime _t0 = DateTime(2026, 9, 27, 16, 13, 19);

Widget _host(List<ToolActivity> tools, {DateTime Function()? now}) =>
    MaterialApp(
      home: Scaffold(
        body: ToolActivityView(tools: tools, now: now ?? () => _t0),
      ),
    );

void main() {
  testWidgets('a pending look says what it is doing and how long it takes',
      (tester) async {
    var now = _t0;
    await tester.pumpWidget(_host(
      [
        ToolActivity(
          id: 'c1',
          name: 'identify_image',
          args: const {'kind': 'auto'},
          startedAt: _t0,
        ),
      ],
      now: () => now,
    ));
    expect(find.text('Looking'), findsOneWidget);
    expect(find.text(lookingHint), findsOneWidget);
    expect(find.textContaining('5–15 seconds'), findsOneWidget);
    expect(find.text('Running…'), findsOneWidget);

    // Three seconds later the card says so (the counter ticks each second).
    now = _t0.add(const Duration(seconds: 3));
    await tester.pump(const Duration(seconds: 1));
    expect(find.text('Running… 3 s'), findsOneWidget);

    await tester.pumpWidget(const SizedBox()); // stops the ticking timer
  });

  testWidgets('capture_photo shows the same card', (tester) async {
    await tester.pumpWidget(_host([
      ToolActivity(
        id: 'c2',
        name: 'capture_photo',
        args: const {},
        startedAt: _t0,
      ),
    ]));
    expect(find.text('Looking'), findsOneWidget);
    expect(find.text(lookingHint), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('a finished look disappears; a card without a start time '
      'still says Running…', (tester) async {
    await tester.pumpWidget(_host([
      const ToolActivity(
        id: 'c3',
        name: 'identify_image',
        args: {},
        ok: true,
        result: {'ok': true},
      ),
      const ToolActivity(id: 'c4', name: 'web_search', args: {'query': 'x'}),
    ]));
    expect(find.text('Looking'), findsNothing);
    expect(find.text('Web search'), findsOneWidget);
    expect(find.text('Running…'), findsOneWidget);
  });
}
