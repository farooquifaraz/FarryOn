import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../core/app_update.dart';
import '../../core/config_store.dart';
import '../../core/theme.dart';
import '../../core/ui.dart';
import '../../state/providers.dart';

/// What [AppUpdate.check] found at start (null until it has answered, or
/// when it couldn't tell).
final appUpdateProvider = StateProvider<UpdateStatus?>((ref) => null);

/// Open the APK download in the browser. Android then shows its own
/// "Install" prompt — a sideloaded app can't install itself silently.
Future<void> openDownload(Uri url) async {
  try {
    await launchUrl(url, mode: LaunchMode.externalApplication);
  } catch (_) {}
}

/// This build is below the oldest one still served. Nothing else of the app
/// is reachable from here: Back does nothing, and the only way on is the new
/// build.
class UpdateRequiredScreen extends StatelessWidget {
  const UpdateRequiredScreen({super.key, required this.downloadUrl});

  final Uri downloadUrl;

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: false,
      child: Scaffold(
        backgroundColor: Aurora.base,
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 28),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Icon(Icons.system_update_rounded,
                    size: 64, color: Aurora.mint),
                const SizedBox(height: 24),
                const Text(
                  'Update required',
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    color: Aurora.textPrimary,
                    fontSize: 24,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 12),
                const Text(
                  'This version of FarryOn is no longer supported. Download '
                  'the new version, tap Install when Android asks, then open '
                  'the app again.',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: Aurora.textMuted, height: 1.45),
                ),
                const SizedBox(height: 32),
                GradientButton(
                  label: 'Download update',
                  icon: Icons.download_rounded,
                  onPressed: () => openDownload(downloadUrl),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// A newer build is on the website. Offered once per build: "Later" is
/// remembered, and the same build is not offered again.
Future<void> offerUpdate(BuildContext context, UpdateStatus status) async {
  final latest = status.latest;
  if (latest == null || ConfigStore.updateOfferDismissed() == latest) return;
  final download = await showDialog<bool>(
    context: context,
    builder: (ctx) => AlertDialog(
      backgroundColor: Aurora.surfaceHigh,
      title: const Text('Update available',
          style: TextStyle(color: Aurora.textPrimary)),
      content: const Text(
        'A new version of FarryOn is ready. Download it and tap Install when '
        'Android asks — your account and settings stay as they are.',
        style: TextStyle(color: Aurora.textMuted, height: 1.4),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(ctx).pop(false),
          child: const Text('Later'),
        ),
        TextButton(
          onPressed: () => Navigator.of(ctx).pop(true),
          child: const Text('Download'),
        ),
      ],
    ),
  );
  if (download == true) {
    await openDownload(status.downloadUrl);
  } else {
    await ConfigStore.markUpdateOfferDismissed(latest);
  }
}

/// Runs [AppUpdate.check] once, shortly after start, and offers what it
/// finds. A check that fails says nothing — no prompt on a guess.
Future<void> runStartupUpdateCheck(
  WidgetRef ref,
  BuildContext? Function() context,
) async {
  final status = await AppUpdate.check(ref.read(configProvider));
  if (status == null) return;
  ref.read(appUpdateProvider.notifier).state = status;
  final ctx = context();
  if (!status.required && status.available && ctx != null && ctx.mounted) {
    await offerUpdate(ctx, status);
  }
}
