/// The chat's captured-photo preview must decode at thumbnail size.
///
/// `width` and `height` on `Image.memory` only lay the picture out — the frame
/// is decoded at its own size first and shrunk afterwards. So an 88x66 preview
/// was costing a full camera bitmap, several megabytes, re-rasterised whenever
/// the chat scrolled past it.
///
/// On a vivo V2246 that read as the app freezing every time it was flicked:
/// fifteen main-thread stalls in three minutes, the worst 1042 frames (~17 s),
/// with the assistant's audio underrunning throughout and the microphone
/// restarting nine times. The user's report was "my voice isn't being picked
/// up" — the microphone was fine; nothing could run while the frame was being
/// decoded (2026-08-19).
library;

import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;

Uint8List _jpeg(int w, int h) {
  final image = img.Image(width: w, height: h);
  img.fill(image, color: img.ColorRgb8(90, 140, 190));
  return Uint8List.fromList(img.encodeJpg(image, quality: 80));
}

void main() {
  testWidgets('a camera-sized frame is decoded down to the preview size',
      (tester) async {
    final photo = _jpeg(1280, 720);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Image.memory(
            photo,
            width: 88,
            height: 66,
            cacheWidth: (88 * 3).round(),
            cacheHeight: (66 * 3).round(),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    final widget = tester.widget<Image>(find.byType(Image));
    final provider = widget.image as ResizeImage;

    expect(provider.width, isNotNull,
        reason: 'without cacheWidth the full 1280x720 bitmap is rasterised');
    expect(provider.width! <= 320, isTrue,
        reason: 'decoded near the size it is actually drawn at');
  });
}
