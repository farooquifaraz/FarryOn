/// The per-frame cost of the phone camera path.
///
/// A live session captures a frame a second for as long as it runs, so
/// anything wasteful here is not wasteful once — it is wasteful sixty times a
/// minute, for an hour. On a vivo V2246 that ended with the UI thread stalled
/// for seven and eight seconds at a time and the app showing a black screen
/// while it was still, underneath, connected and recording (device-seen
/// 2026-08-15).
library;

import 'dart:typed_data';

import 'package:farryon/capture/phone_capture_source.dart';
import 'package:farryon/protocol/protocol.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;

Uint8List _jpeg(int width, int height) {
  final image = img.Image(width: width, height: height);
  // Some content, so the encoder cannot collapse it to something degenerate.
  img.fill(image, color: img.ColorRgb8(120, 160, 200));
  return Uint8List.fromList(img.encodeJpg(image, quality: 80));
}

void main() {
  group('deciding whether a frame needs resizing at all', () {
    test('a frame already within the limit is left alone', () {
      // The real case, and the one that was being missed: the camera runs at
      // ResolutionPreset.medium, so its own JPEG is comfortably inside the
      // limit and was still being decoded and re-encoded every second.
      expect(jpegFitsWithin(_jpeg(640, 480), VideoFormat.maxWidth), isTrue);
    });

    test('a frame exactly at the limit is left alone', () {
      // <=, not <. A frame the same width as the target gains nothing from a
      // resize, and re-encoding it would only lose quality.
      expect(
        jpegFitsWithin(_jpeg(VideoFormat.maxWidth, 720), VideoFormat.maxWidth),
        isTrue,
      );
    });

    test('an oversized frame still goes to the downscaler', () {
      expect(jpegFitsWithin(_jpeg(2400, 1080), VideoFormat.maxWidth), isFalse);
    });

    test('the long edge decides, whichever way up the frame is', () {
      // Portrait: height is what exceeds the limit. Measuring width alone
      // would wave through a frame twice the intended size.
      expect(jpegFitsWithin(_jpeg(720, 2400), VideoFormat.maxWidth), isFalse);
    });

    test('a truncated JPEG is refused, header or no header', () {
      // The one that got through. A file read while it was still being written
      // has a perfectly good header — dimensions and all — and no ending. The
      // header is all this check reads, so it waved the frame past, and a
      // half-written picture went to the model and to the chat preview, where
      // Android's decoder answered "Input contained an error" (vivo V2246,
      // 2026-08-19).
      //
      // The full decode used to catch this by accident, by failing. Skipping
      // the decode to save the work lost the accident with it.
      final whole = _jpeg(640, 480);
      expect(jpegFitsWithin(whole, VideoFormat.maxWidth), isTrue);

      final cut = Uint8List.sublistView(whole, 0, whole.length - 64);
      expect(jpegFitsWithin(cut, VideoFormat.maxWidth), isFalse,
          reason: 'no end-of-image marker — the file is not finished');
    });

    test('bytes that are not a readable JPEG take the old path', () {
      // Deliberately false rather than true: a frame we could not measure is
      // sent to the decoder, which either handles it or reports failure. The
      // one thing we must not do is pass unmeasured bytes off as being within
      // a limit we never checked.
      expect(jpegFitsWithin(Uint8List.fromList([1, 2, 3, 4]), 1280), isFalse);
      expect(jpegFitsWithin(Uint8List(0), 1280), isFalse);
    });
  });
}
