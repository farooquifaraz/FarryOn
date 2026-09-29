/// Settings → Glasses: restart, factory reset and About (2026-09-29),
/// modelled on the vendor app's "My glasses" screen.
library;

import 'package:farryon/features/settings/glasses_page.dart';
import 'package:farryon/state/live_state.dart';
import 'package:farryon/state/providers.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// A live notifier with a fixed state that records the glasses commands.
class _FakeLive extends LiveNotifier {
  _FakeLive(this.initial);
  final LiveSessionState initial;
  final calls = <String>[];

  @override
  LiveSessionState build() => initial;

  @override
  Future<bool> restartGlasses() async {
    calls.add('restart');
    return state.glassesConnected;
  }

  @override
  Future<bool> factoryResetGlasses() async {
    calls.add('factoryReset');
    return state.glassesConnected;
  }

  @override
  Future<bool> refreshGlassesInfo() async {
    calls.add('refreshInfo');
    return true;
  }

  @override
  Future<void> disconnectGlasses() async => calls.add('disconnect');
}

const _connected = LiveSessionState(
  glassesConnected: true,
  glassesName: 'SANVNET GS4 MAX_2BAB',
  glassesMac: '63:08:F1:2B:2B:AB',
  glassesBattery: 100,
  glassesInfo: {
    'btFirmware': '2.20.16_260424',
    'btHardware': 'AM01W_V2.2',
    'wifiFirmware': '1.00.28_2601152230',
    'wifiHardware': 'WIFIAM01W_V2.2',
  },
);

Widget _host(Widget page, _FakeLive live) => ProviderScope(
      overrides: [liveProvider.overrideWith(() => live)],
      child: MaterialApp(home: page),
    );

void main() {
  testWidgets('the page shows the pair and its three actions', (tester) async {
    final live = _FakeLive(_connected);
    await tester.pumpWidget(_host(const GlassesPage(), live));
    expect(find.text('SANVNET GS4 MAX_2BAB'), findsOneWidget);
    expect(find.textContaining('63:08:F1:2B:2B:AB'), findsOneWidget);
    expect(find.text('Connected · 100%'), findsOneWidget);
    expect(find.text('Restart'), findsOneWidget);
    expect(find.text('Restore factory settings'), findsOneWidget);
    expect(find.text('About'), findsOneWidget);
    expect(find.text('Disconnect'), findsOneWidget);
  });

  testWidgets('Restart asks first, in the middle, then sends the command',
      (tester) async {
    final live = _FakeLive(_connected);
    await tester.pumpWidget(_host(const GlassesPage(), live));
    await tester.tap(find.text('Restart'));
    await tester.pumpAndSettle();
    expect(find.text('Restart the glasses?'), findsOneWidget);
    expect(find.byType(Dialog), findsOneWidget,
        reason: 'a centred dialog, not a bottom sheet');
    expect(live.calls, isEmpty, reason: 'nothing sent before the answer');

    await tester.tap(find.widgetWithText(FilledButton, 'Restart'));
    await tester.pumpAndSettle();
    expect(live.calls, ['restart']);
    expect(find.textContaining('reconnect in about 20 seconds'), findsOneWidget);
  });

  testWidgets('Cancel on the reset dialog sends nothing', (tester) async {
    final live = _FakeLive(_connected);
    await tester.pumpWidget(_host(const GlassesPage(), live));
    await tester.tap(find.text('Restore factory settings'));
    await tester.pumpAndSettle();
    expect(find.text('Restore factory settings?'), findsOneWidget);
    expect(find.text('Erase & reset'), findsOneWidget);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(live.calls, isEmpty);
  });

  testWidgets('Erase & reset sends the command and leaves the page',
      (tester) async {
    final live = _FakeLive(_connected);
    await tester.pumpWidget(ProviderScope(
      overrides: [liveProvider.overrideWith(() => live)],
      child: MaterialApp(
        home: Builder(
          builder: (ctx) => Scaffold(
            body: TextButton(
              onPressed: () => Navigator.of(ctx).push(
                MaterialPageRoute<void>(builder: (_) => const GlassesPage()),
              ),
              child: const Text('open'),
            ),
          ),
        ),
      ),
    ));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Restore factory settings'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Erase & reset'));
    await tester.pumpAndSettle();
    expect(live.calls, ['factoryReset']);
    expect(find.text('My glasses'), findsNothing, reason: 'popped after reset');
  });

  testWidgets('without a connection the commands are off', (tester) async {
    final live = _FakeLive(const LiveSessionState(glassesName: 'GS4 MAX'));
    await tester.pumpWidget(_host(const GlassesPage(), live));
    expect(find.text('Connect the glasses first'), findsNWidgets(2));
    await tester.tap(find.text('Restart'));
    await tester.pumpAndSettle();
    expect(find.text('Restart the glasses?'), findsNothing);
    expect(find.text('Disconnect'), findsNothing);
  });

  testWidgets('About lists what the glasses reported and asks on open',
      (tester) async {
    final live = _FakeLive(_connected);
    await tester.pumpWidget(_host(const GlassesAboutPage(), live));
    await tester.pumpAndSettle();
    expect(live.calls, ['refreshInfo']);
    expect(find.text('Bluetooth name'), findsOneWidget);
    expect(find.text('SANVNET GS4 MAX_2BAB'), findsOneWidget);
    expect(find.text('63:08:F1:2B:2B:AB'), findsOneWidget);
    expect(find.text('AM01W_V2.2'), findsOneWidget);
    expect(find.text('2.20.16_260424'), findsOneWidget);
    expect(find.text('WIFIAM01W_V2.2'), findsOneWidget);
    expect(find.text('1.00.28_2601152230'), findsOneWidget);
    expect(find.text('100% · charged'), findsOneWidget);
    expect(find.text('Refresh from glasses'), findsOneWidget);
  });

  testWidgets('About shows dashes for what was never reported',
      (tester) async {
    final live = _FakeLive(const LiveSessionState(glassesName: 'GS4 MAX'));
    await tester.pumpWidget(_host(const GlassesAboutPage(), live));
    await tester.pumpAndSettle();
    expect(find.text('—'), findsWidgets);
    expect(find.text('Connect the glasses to refresh'), findsOneWidget);
  });
}
