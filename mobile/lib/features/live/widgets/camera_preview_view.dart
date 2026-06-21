import 'package:camera/camera.dart';
import 'package:flutter/material.dart';

import '../../../capture/capture_source.dart';
import '../../../capture/phone_capture_source.dart';
import '../../../core/theme.dart';

/// Live camera preview for the active [CaptureSource].
///
/// Only the [PhoneCaptureSource] exposes a renderable `CameraController`; for
/// other sources (e.g. the glasses stub, whose video arrives as decoded JPEGs
/// over a transport) we show a neutral placeholder. This keeps the UI honest
/// about what the active device can actually preview.
class CameraPreviewView extends StatelessWidget {
  const CameraPreviewView({
    super.key,
    required this.source,
    required this.enabled,
    this.portrait = true,
  });

  final CaptureSource source;
  final bool enabled;

  /// Whether to lay the preview out as portrait (fills a tall hero) or
  /// landscape. Must match the orientation locked on the [CaptureSource].
  final bool portrait;

  @override
  Widget build(BuildContext context) {
    if (!enabled) {
      return const _Placeholder(
        icon: Icons.videocam_off,
        label: 'Camera off',
      );
    }

    final src = source;
    if (src is PhoneCaptureSource) {
      final controller = src.cameraController;
      if (controller != null && controller.value.isInitialized) {
        // Cover-fill the hero. The sensor preview is landscape, so for a
        // portrait layout we swap width/height before the BoxFit.cover.
        final preview = controller.value.previewSize;
        final w = preview?.width ?? 16;
        final h = preview?.height ?? 9;
        return ClipRRect(
          borderRadius: BorderRadius.circular(18),
          child: SizedBox.expand(
            child: FittedBox(
              fit: BoxFit.cover,
              child: SizedBox(
                width: portrait ? h : w,
                height: portrait ? w : h,
                child: CameraPreview(controller),
              ),
            ),
          ),
        );
      }
      return const _Placeholder(
        icon: Icons.photo_camera,
        label: 'Starting camera…',
      );
    }

    // Glasses or other transport-fed source: no local preview surface.
    return _Placeholder(
      icon: Icons.visibility,
      label: 'Streaming from ${src.info.kind}',
    );
  }
}

class _Placeholder extends StatelessWidget {
  const _Placeholder({required this.icon, required this.label});

  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: Aurora.surface,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: Aurora.glassBorder),
      ),
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 40, color: Aurora.textMuted),
            const SizedBox(height: 8),
            Text(
              label,
              style: const TextStyle(color: Aurora.textMuted, fontSize: 14),
            ),
          ],
        ),
      ),
    );
  }
}
