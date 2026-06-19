import 'package:camera/camera.dart';
import 'package:flutter/material.dart';

import '../../../capture/capture_source.dart';
import '../../../capture/phone_capture_source.dart';

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
  });

  final CaptureSource source;
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    if (!enabled) {
      return _Placeholder(
        icon: Icons.videocam_off,
        label: 'Camera off',
        color: theme.colorScheme.surfaceContainerHighest,
      );
    }

    final src = source;
    if (src is PhoneCaptureSource) {
      final controller = src.cameraController;
      if (controller != null && controller.value.isInitialized) {
        return ClipRRect(
          borderRadius: BorderRadius.circular(16),
          child: CameraPreview(controller),
        );
      }
      return _Placeholder(
        icon: Icons.photo_camera,
        label: 'Starting camera…',
        color: theme.colorScheme.surfaceContainerHighest,
      );
    }

    // Glasses or other transport-fed source: no local preview surface.
    return _Placeholder(
      icon: Icons.visibility,
      label: 'Streaming from ${src.info.kind}',
      color: theme.colorScheme.surfaceContainerHighest,
    );
  }
}

class _Placeholder extends StatelessWidget {
  const _Placeholder({
    required this.icon,
    required this.label,
    required this.color,
  });

  final IconData icon;
  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: color,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 40, color: Theme.of(context).colorScheme.outline),
            const SizedBox(height: 8),
            Text(label, style: Theme.of(context).textTheme.bodyMedium),
          ],
        ),
      ),
    );
  }
}
