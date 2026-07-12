import 'dart:typed_data';

import 'package:flutter/services.dart';

import 'logger.dart';

/// Saves captured images (phone camera / smart-glasses stills) into the phone
/// gallery via the native `com.farryon/media` channel (MediaStore →
/// `Pictures/Farry`). Best-effort: a failure is logged, never thrown, so a
/// gallery hiccup can't disrupt the live session.
class MediaSaver {
  MediaSaver._();

  static const MethodChannel _channel = MethodChannel('com.farryon/media');
  static final _log = Logger('MediaSaver');

  /// Persist [jpeg] to the gallery. Returns the saved content URI, or null on
  /// failure. [name] defaults to a timestamped `Farry_<ms>.jpg`.
  static Future<String?> saveImage(Uint8List jpeg, {String? name}) async {
    if (jpeg.isEmpty) return null;
    try {
      final uri = await _channel.invokeMethod<String>('saveImageToGallery', {
        'bytes': jpeg,
        'name': name ?? 'Farry_${DateTime.now().millisecondsSinceEpoch}.jpg',
      });
      _log.info('saved capture to gallery (${jpeg.length} bytes)');
      return uri;
    } catch (e) {
      _log.warn('gallery save failed: $e');
      return null;
    }
  }
}
