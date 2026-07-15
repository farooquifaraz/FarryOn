import 'package:flutter/material.dart';

import '../../core/theme.dart';
import 'bridge/glasses_channel.dart';
import 'glasses_lab_controller.dart';
import 'widgets/audio_card.dart';
import 'widgets/camera_card.dart';
import 'widgets/connection_card.dart';
import 'widgets/device_info_card.dart';
import 'widgets/event_console_card.dart';
import 'widgets/media_sync_card.dart';

/// Glasses Lab — the isolated hardware test bench for the L801 smart glasses
/// (Stage A of the glasses integration plan).
///
/// One card per SDK capability: connection, device info, camera/photo-trigger,
/// audio paths, media sync, event console. The Lab is a debug-only, fully
/// self-contained module: it owns its controller and platform bridge and
/// touches nothing in the live/capture/data layers, so it can never
/// destabilise the shipping app. When every card is green (see the plan's
/// Stage A exit gate) the proven bridge code graduates into
/// `lib/capture/glasses_capture_source.dart` in Stage B.
class GlassesLabScreen extends StatefulWidget {
  const GlassesLabScreen({super.key});

  static Future<void> open(BuildContext context) => Navigator.of(context).push(
        MaterialPageRoute<void>(builder: (_) => const GlassesLabScreen()),
      );

  @override
  State<GlassesLabScreen> createState() => _GlassesLabScreenState();
}

class _GlassesLabScreenState extends State<GlassesLabScreen> {
  late final GlassesLabController _controller;

  @override
  void initState() {
    super.initState();
    _controller = GlassesLabController(GlassesChannel());
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Glasses Lab')),
      body: ListenableBuilder(
        listenable: _controller,
        builder: (context, _) {
          final c = _controller;
          return ListView(
            padding: const EdgeInsets.symmetric(vertical: 8),
            children: [
              _BridgeBanner(c),
              ConnectionCard(c),
              DeviceInfoCard(c),
              CameraCard(c),
              AudioCard(c),
              MediaSyncCard(c),
              EventConsoleCard(c),
              const SizedBox(height: 24),
            ],
          );
        },
      ),
    );
  }
}

/// Top banner: which native implementation is answering (HeyCyan SDK vs the
/// built-in stub) — so nobody mistakes simulated results for hardware truth.
class _BridgeBanner extends StatelessWidget {
  const _BridgeBanner(this.c);

  final GlassesLabController c;

  @override
  Widget build(BuildContext context) {
    final (text, color) = switch (c.bridgeImplementation) {
      'heycyan' => ('HeyCyan SDK ${c.sdkVersion}', Aurora.teal),
      'stub' => (
          'STUB MODE — simulated device, no hardware. Drop the vendor .aar '
          'into android/app/libs/ and wire HeyCyanGlassesSdk to go live.',
          Aurora.amber
        ),
      'unavailable' => (
          'Native bridge not available on this platform.',
          Aurora.danger
        ),
      _ => ('Checking native bridge…', Aurora.textMuted),
    };
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Aurora.tint(color, 0.10),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Aurora.tint(color, 0.35)),
      ),
      child: Row(
        children: [
          Icon(Icons.science_outlined, size: 18, color: color),
          const SizedBox(width: 8),
          Expanded(
            child: Text(text,
                style: TextStyle(fontSize: 12.5, color: color, height: 1.3)),
          ),
        ],
      ),
    );
  }
}
