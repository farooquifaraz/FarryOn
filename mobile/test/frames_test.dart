import 'dart:typed_data';

import 'package:farryon/protocol/frames.dart';
import 'package:farryon/protocol/protocol.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('MediaFrame header', () {
    test('header is exactly 9 bytes (uint8 tag + uint64 ts)', () {
      expect(MediaFrame.headerSize, 9);
      final frame = MediaFrame.encode(
        tag: FrameTag.inputAudio,
        timestampMs: 0,
        payload: Uint8List(0),
      );
      expect(frame.length, 9);
    });

    test('known byte vector — little-endian timestamp layout', () {
      // tag=0x01, ts=1 (ms), payload=[0xAA,0xBB].
      // Expected bytes: [0x01,  0x01,0x00,0x00,0x00,0x00,0x00,0x00,0x00,  0xAA,0xBB]
      // The 8-byte timestamp is LITTLE-endian: low byte first.
      final frame = MediaFrame.encode(
        tag: FrameTag.inputAudio,
        timestampMs: 1,
        payload: Uint8List.fromList([0xAA, 0xBB]),
      );
      expect(
        frame,
        equals(Uint8List.fromList([
          0x01, // tag
          0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, // ts=1 LE
          0xAA, 0xBB, // payload
        ])),
      );
    });

    test('known byte vector — multi-byte timestamp stays little-endian', () {
      // ts = 0x0102030405060708 → LE bytes are 08 07 06 05 04 03 02 01.
      const ts = 0x0102030405060708;
      final frame = MediaFrame.encode(
        tag: FrameTag.outputAudio,
        timestampMs: ts,
        payload: Uint8List(0),
      );
      expect(
        frame.sublist(1, 9),
        equals(Uint8List.fromList([
          0x08, 0x07, 0x06, 0x05, 0x04, 0x03, 0x02, 0x01,
        ])),
      );
      // And the tag byte.
      expect(frame[0], FrameTag.outputAudio);
    });
  });

  group('round-trip', () {
    test('encode → decode preserves tag, ts, and payload', () {
      final payload = Uint8List.fromList(
        List<int>.generate(256, (i) => i & 0xFF),
      );
      const ts = 1718764800123; // realistic ms-since-epoch
      final encoded = MediaFrame.encode(
        tag: FrameTag.inputVideo,
        timestampMs: ts,
        payload: payload,
      );
      final decoded = MediaFrame.decode(encoded);

      expect(decoded.tag, FrameTag.inputVideo);
      expect(decoded.timestampMs, ts);
      expect(decoded.payload, equals(payload));
    });

    test('empty payload round-trips', () {
      final encoded = MediaFrame.encode(
        tag: FrameTag.inputAudio,
        timestampMs: 42,
        payload: Uint8List(0),
      );
      final decoded = MediaFrame.decode(encoded);
      expect(decoded.timestampMs, 42);
      expect(decoded.payload, isEmpty);
    });

    test('decode accepts a plain List<int> (web_socket_channel payload)', () {
      final encoded = MediaFrame.encode(
        tag: FrameTag.outputAudio,
        timestampMs: 7,
        payload: Uint8List.fromList([1, 2, 3]),
      );
      // Simulate the loosely-typed bytes some channels deliver.
      final asList = List<int>.from(encoded);
      final decoded = MediaFrame.decode(asList);
      expect(decoded.tag, FrameTag.outputAudio);
      expect(decoded.timestampMs, 7);
      expect(decoded.payload, equals(Uint8List.fromList([1, 2, 3])));
    });

    test('large timestamp near current epoch round-trips exactly', () {
      final ts = DateTime.now().millisecondsSinceEpoch;
      final decoded = MediaFrame.decode(
        MediaFrame.encode(
          tag: FrameTag.inputAudio,
          timestampMs: ts,
          payload: Uint8List(0),
        ),
      );
      expect(decoded.timestampMs, ts);
    });
  });

  group('validation', () {
    test('rejects a tag outside one byte', () {
      expect(
        () => MediaFrame.encode(
          tag: 256,
          timestampMs: 0,
          payload: Uint8List(0),
        ),
        throwsArgumentError,
      );
    });

    test('rejects a negative timestamp', () {
      expect(
        () => MediaFrame.encode(
          tag: FrameTag.inputAudio,
          timestampMs: -1,
          payload: Uint8List(0),
        ),
        throwsArgumentError,
      );
    });

    test('decode rejects a buffer shorter than the header', () {
      expect(
        () => MediaFrame.decode(Uint8List.fromList([0x01, 0x00])),
        throwsFormatException,
      );
    });
  });
}
