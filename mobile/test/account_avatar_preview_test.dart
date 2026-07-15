@Tags(['preview'])
library;

import 'package:farryon/core/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// Renders the account avatar to a PNG so the design can be LOOKED at:
///   flutter test --run-skipped --update-goldens test/account_avatar_preview_test.dart
/// then open test/goldens/account_avatar.png.
///
/// Skipped by default and gitignored, like the auth previews — golden bytes
/// differ per host and would fail in CI for no useful reason. This exists to
/// review the look, not to gate it.
void main() {
  testWidgets('account avatar preview', (tester) async {
    tester.view.physicalSize = const Size(900, 300);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.reset);

    Widget avatar(String initial) => Container(
          width: 28,
          height: 28,
          alignment: Alignment.center,
          decoration: const BoxDecoration(
            shape: BoxShape.circle,
            gradient: Aurora.gradPink,
          ),
          child: Text(
            initial,
            style: const TextStyle(
              fontSize: 13,
              fontWeight: FontWeight.w800,
              color: Colors.white,
              height: 1.0,
            ),
          ),
        );

    await tester.pumpWidget(MaterialApp(
      theme: Aurora.theme(),
      home: Scaffold(
        backgroundColor: const Color(0xFF101820),
        body: Center(
          // The avatar as it sits in the live screen's top bar: on the same
          // translucent black pill, beside the "more actions" toggle.
          child: Container(
            padding: const EdgeInsets.fromLTRB(12, 6, 6, 6),
            decoration: BoxDecoration(
              color: Colors.black.withValues(alpha: 0.42),
              borderRadius: BorderRadius.circular(20),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Text('LIVE',
                    style: TextStyle(color: Colors.white, fontSize: 11)),
                const SizedBox(width: 40),
                for (final i in ['F', 'Z', 'फ़', '•']) ...[
                  Padding(padding: const EdgeInsets.all(5), child: avatar(i)),
                ],
                const Icon(Icons.more_horiz_rounded,
                    color: Colors.white, size: 22),
              ],
            ),
          ),
        ),
      ),
    ));
    await tester.pumpAndSettle();

    await expectLater(
      find.byType(Scaffold),
      matchesGoldenFile('goldens/account_avatar.png'),
    );
  });
}
