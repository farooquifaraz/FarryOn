/// The scan button has to be pressable with the camera off.
///
/// It used to be greyed out unless the camera was already running, which was
/// reasonable while the camera opened with the session: "off" then meant the
/// user had turned it off, and the button was telling them so.
///
/// The camera no longer opens with the session, so off is simply where it
/// starts — and the greyed-out button became a dead control on a phone whose
/// camera works, with nothing on screen saying why. Found on a vivo V2246
/// (2026-08-15): the voice route reached the camera and the button did not,
/// because the fix was in the controller and the gate was in the UI.
library;

import 'package:farryon/features/live/live_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('the scan button is pressable while the camera is off',
      (tester) async {
    await tester.pumpWidget(
      const ProviderScope(child: MaterialApp(home: LiveScreen())),
    );
    await tester.pump();

    final scan = find.byTooltip('Identify what the camera sees');
    expect(scan, findsOneWidget, reason: 'the button should be on screen');

    // _CircleButton renders a Material + InkWell rather than an IconButton,
    // and a null `onTap` is exactly what "greyed out" means here.
    final tap = tester.widget<InkWell>(
      find.descendant(of: scan, matching: find.byType(InkWell)),
    );
    expect(
      tap.onTap,
      isNotNull,
      reason: 'a dead button is worse than a slow one — tapping it is what '
          'turns the camera on now',
    );
  });
}
