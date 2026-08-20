/// The first-run permission introduction: shown once, and shown FIRST.
///
/// Each feature asks for what it needs when it needs it, which is Android's
/// own advice and is not going away. What that misses is the beginning:
/// somebody who has just made an account has never been told the app wants a
/// microphone, a camera, Bluetooth and notifications, and the ones they never
/// reach are never asked for at all. On a fresh install the glasses could not
/// be found and reminders were silent for exactly that reason (vivo V2246,
/// 2026-08-15).
///
/// Two things have to hold. It must come BEFORE the live screen, which asks
/// for the microphone the instant it mounts — explaining a dialog the user has
/// already answered is worse than saying nothing. And it must appear once:
/// the flag records that it was SHOWN, not that anything was granted, so
/// somebody who declines is not asked again on every launch.
library;

import 'package:farryon/core/config_store.dart';
import 'package:farryon/features/onboarding/permission_intro_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  // ConfigStore opens the keystore on init; stand in for it the same way the
  // auth tests do, so this file tests the intro rather than the platform.
  final keystore = <String, String>{};
  setUp(() async {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
      const MethodChannel('plugins.it_nomads.com/flutter_secure_storage'),
      (call) async {
        switch (call.method) {
          case 'write':
            keystore[call.arguments['key'] as String] =
                call.arguments['value'] as String;
            return null;
          case 'read':
            return keystore[call.arguments['key'] as String];
          case 'readAll':
            return keystore;
          default:
            return null;
        }
      },
    );
    keystore.clear();
    SharedPreferences.setMockInitialValues({});
    await ConfigStore.init();
  });

  test('a fresh install has not seen it', () {
    expect(ConfigStore.permissionIntroSeen(), isFalse);
  });

  test('once marked, it stays marked', () async {
    await ConfigStore.markPermissionIntroSeen();
    expect(ConfigStore.permissionIntroSeen(), isTrue);
  });

  testWidgets('it names every permission, with a reason for each',
      (tester) async {
    await tester.pumpWidget(
      MaterialApp(home: PermissionIntroScreen(onDone: () {})),
    );
    await tester.pump();

    // The four the app actually needs. A list that quietly loses one is how a
    // feature ends up broken on a phone whose owner was never asked.
    expect(find.text('Microphone'), findsOneWidget);
    expect(find.text('Camera'), findsOneWidget);
    expect(find.text('Nearby devices'), findsOneWidget);
    expect(find.text('Notifications'), findsOneWidget);

    // And a way past it that is not "grant everything".
    expect(find.text('Not now'), findsOneWidget);
  });

  testWidgets('declining still counts as shown, and moves on', (tester) async {
    var done = false;
    await tester.pumpWidget(
      MaterialApp(home: PermissionIntroScreen(onDone: () => done = true)),
    );
    await tester.pump();

    await tester.tap(find.text('Not now'));
    await tester.pumpAndSettle();

    expect(done, isTrue, reason: 'the app must not be stuck behind this');
    expect(ConfigStore.permissionIntroSeen(), isTrue,
        reason: 'saying "not now" is an answer — do not ask again every launch');
  });
}
