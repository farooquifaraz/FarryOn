import 'dart:typed_data';

import 'protocol.dart';

/// Codec for the fixed binary media-frame header defined in `PROTOCOL.md` §2.
///
/// Wire layout (little-endian, no length field — WebSocket frames are already
/// length-delimited):
///
/// ```
///  offset  size  field
///  ------  ----  ----------------------------------------------
///    0      1    tag   (uint8)   — stream type (see [FrameTag])
///    1      8    ts    (uint64)  — capture/emit time, ms since epoch (LE)
///    9      ..   payload         — raw bytes (PCM or JPEG)
/// ```
///
/// This is the most contract-sensitive code in the app: it must produce and
/// consume bytes identically to the Python backend. Round-trip and
/// known-vector tests live in `test/frames_test.dart`.
abstract final class MediaFrame {
  /// Size of the fixed header in bytes (`uint8` tag + `uint64` timestamp).
  static const int headerSize = 9;

  /// Offset of the timestamp field within the header.
  static const int _tsOffset = 1;

  /// Encode a media frame into a single contiguous [Uint8List].
  ///
  /// [tag] is one of [FrameTag.inputAudio] / [FrameTag.inputVideo] /
  /// [FrameTag.outputAudio]. [timestampMs] is milliseconds since the Unix epoch
  /// and is written as an unsigned 64-bit little-endian integer. [payload] is
  /// the raw media bytes (PCM16 or JPEG) appended verbatim.
  ///
  /// Throws [ArgumentError] if [tag] does not fit in a single byte or
  /// [timestampMs] is negative.
  static Uint8List encode({
    required int tag,
    required int timestampMs,
    required Uint8List payload,
  }) {
    if (tag < 0 || tag > 0xFF) {
      throw ArgumentError.value(tag, 'tag', 'must be a single byte (0–255)');
    }
    if (timestampMs < 0) {
      throw ArgumentError.value(
        timestampMs,
        'timestampMs',
        'must be non-negative',
      );
    }

    final out = Uint8List(headerSize + payload.length);
    final header = ByteData.sublistView(out, 0, headerSize);
    header.setUint8(0, tag);
    // setUint64 with Endian.little writes the 8-byte LE timestamp at offset 1.
    header.setUint64(_tsOffset, timestampMs, Endian.little);
    out.setRange(headerSize, out.length, payload);
    return out;
  }

  /// Convenience wrapper that stamps the frame with the current wall-clock time.
  static Uint8List encodeNow({
    required int tag,
    required Uint8List payload,
  }) =>
      encode(
        tag: tag,
        timestampMs: DateTime.now().millisecondsSinceEpoch,
        payload: payload,
      );

  /// Decode a media frame received from the wire.
  ///
  /// Accepts any [List<int>] (the `web_socket_channel` binary payload type) and
  /// returns a [DecodedFrame]. The returned [DecodedFrame.payload] is a zero-copy
  /// view over [bytes] when possible, so do not mutate [bytes] afterwards.
  ///
  /// Throws [FormatException] if the buffer is shorter than [headerSize].
  static DecodedFrame decode(List<int> bytes) {
    final data = bytes is Uint8List ? bytes : Uint8List.fromList(bytes);
    if (data.length < headerSize) {
      throw FormatException(
        'media frame too short: ${data.length} bytes '
        '(need at least $headerSize)',
      );
    }
    final header = ByteData.sublistView(data, 0, headerSize);
    final tag = header.getUint8(0);
    final ts = header.getUint64(_tsOffset, Endian.little);
    final payload = Uint8List.sublistView(data, headerSize);
    return DecodedFrame(tag: tag, timestampMs: ts, payload: payload);
  }
}

/// A decoded binary media frame: its [tag], [timestampMs], and [payload].
class DecodedFrame {
  const DecodedFrame({
    required this.tag,
    required this.timestampMs,
    required this.payload,
  });

  /// Stream type — one of the [FrameTag] constants.
  final int tag;

  /// Capture/emit time in milliseconds since the Unix epoch.
  final int timestampMs;

  /// Raw media bytes (PCM16 or JPEG), a view over the source buffer.
  final Uint8List payload;

  @override
  String toString() =>
      'DecodedFrame(tag: 0x${tag.toRadixString(16).padLeft(2, '0')}, '
      'ts: $timestampMs, payload: ${payload.length}B)';
}
