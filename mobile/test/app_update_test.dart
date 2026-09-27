/// The app finds a newer build on the website, or learns it is too old.
///
/// Faraz, 2026-09-26: the app is sideloaded, so an old copy has to be made
/// to update. At start it reads /download/info (the site's build and the
/// oldest build still served); the server also refuses a too-old build at
/// hello with `update_required`.
library;

import 'dart:convert';

import 'package:farryon/core/app_update.dart';
import 'package:farryon/core/config.dart';
import 'package:farryon/core/config_store.dart';
import 'package:farryon/features/update/update_screens.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

const _config = AppConfig(host: 'farryon.test', port: 443, secure: true);

http.Client _site(Map<String, dynamic> info, {int status = 200}) =>
    MockClient((req) async {
      expect(req.url.toString(), 'https://farryon.test/download/info');
      return http.Response(jsonEncode(info), status);
    });

Map<String, dynamic> _info({int build = 2540, int minBuild = 0}) => {
      'version': '1.0.0',
      'minBuild': minBuild,
      'build': {'build': build},
      'arm64': {'available': true, 'size': 1},
      'arm32': {'available': true, 'size': 1},
    };

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() async {
    // ConfigStore reads the keystore at init; stand in for the plugin.
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
      const MethodChannel('plugins.it_nomads.com/flutter_secure_storage'),
      (call) async => null,
    );
    SharedPreferences.setMockInitialValues({});
    await ConfigStore.init();
  });

  test('the base build comes off the versionCode, per ABI', () {
    expect(AppUpdate.baseBuild('1.0.0+4531', 'arm64'), 2531);
    expect(AppUpdate.baseBuild('1.0.0+3531', 'arm32'), 2531);
    expect(AppUpdate.baseBuild('1.0.0+4531', ''), 2531, reason: 'from the thousands');
    expect(AppUpdate.baseBuild('1.0.0+3531', ''), 2531);
    expect(AppUpdate.baseBuild('1.0.0', 'arm64'), isNull);
    expect(AppUpdate.baseBuild('1.0.0+x', 'arm64'), isNull);
  });

  test('a newer build on the site is offered', () async {
    final s = await AppUpdate.check(_config,
        client: _site(_info(build: 2540)),
        installedVersion: '1.0.0+4531',
        abiOverride: 'arm64');
    expect(s, isNotNull);
    expect(s!.current, 2531);
    expect(s.available, isTrue);
    expect(s.required, isFalse);
    expect(s.downloadUrl.toString(), 'https://farryon.test/download/arm64');
  });

  test('the same build is not offered', () async {
    final s = await AppUpdate.check(_config,
        client: _site(_info(build: 2531)),
        installedVersion: '1.0.0+4531',
        abiOverride: 'arm64');
    expect(s!.available, isFalse);
  });

  test('below the minimum it is required', () async {
    final s = await AppUpdate.check(_config,
        client: _site(_info(build: 2550, minBuild: 2540)),
        installedVersion: '1.0.0+3531',
        abiOverride: 'arm32');
    expect(s!.required, isTrue);
    expect(s.downloadUrl.toString(), 'https://farryon.test/download/arm32');
  });

  test('no answer, no prompt', () async {
    expect(
      await AppUpdate.check(_config,
          client: _site(_info(), status: 500),
          installedVersion: '1.0.0+4531',
          abiOverride: 'arm64'),
      isNull,
    );
    expect(
      await AppUpdate.check(_config,
          client: MockClient((_) async => throw Exception('offline')),
          installedVersion: '1.0.0+4531',
          abiOverride: 'arm64'),
      isNull,
    );
    expect(
      await AppUpdate.check(_config,
          client: _site(_info()), installedVersion: '1.0.0', abiOverride: 'arm64'),
      isNull,
      reason: 'a build number it cannot read is never judged',
    );
  });

  testWidgets('the required screen has a way out and no way back',
      (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: UpdateRequiredScreen(
          downloadUrl: Uri.parse('https://farryon.test/download/arm64')),
    ));
    expect(find.text('Update required'), findsOneWidget);
    expect(find.text('Download update'), findsOneWidget);
    final scope = tester.widget<PopScope>(find.byType(PopScope));
    expect(scope.canPop, isFalse);
  });

  testWidgets('Later is remembered for that build', (tester) async {
    await _openOffer(tester);
    await tester.tap(find.text('Later'));
    await tester.pumpAndSettle();
    expect(ConfigStore.updateOfferDismissed(), 2540);
  });

  testWidgets('a dialog the app closed itself is offered again', (tester) async {
    // Vivo 2026-09-27: the sign-in step popped every route above home and
    // took the update dialog with it; that used to count as "Later".
    await _openOffer(tester);
    Navigator.of(tester.element(find.text('Update available'))).pop();
    await tester.pumpAndSettle();
    expect(ConfigStore.updateOfferDismissed(), 0);
  });
}

UpdateStatus _newer() => UpdateStatus(
      current: 2539,
      latest: 2540,
      minimum: 0,
      downloadable: true,
      downloadUrl: Uri.parse('https://farryon.test/download/arm64'),
    );

Future<void> _openOffer(WidgetTester tester) async {
  await tester.pumpWidget(MaterialApp(
    home: Builder(
      builder: (context) => TextButton(
        onPressed: () => offerUpdate(context, _newer()),
        child: const Text('go'),
      ),
    ),
  ));
  await tester.tap(find.text('go'));
  await tester.pumpAndSettle();
  expect(find.text('Update available'), findsOneWidget);
}

