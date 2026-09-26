/// The glasses pill shows the model only. The full advertised name ("SNT GS5
/// MAX_9475") pushed the status row past the screen edge and cut the battery
/// off (device 2026-09-26).
library;

import 'package:farryon/features/live/live_screen.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('L80x names keep their existing short form', () {
    expect(glassesPillName('L802_2B1D'), 'L802');
    expect(glassesPillName('L 801_DD8A'), 'L801');
    expect(glassesPillName('L801-03BC'), 'L801-03BC');
  });

  test('GS names drop the vendor prefix and the serial', () {
    expect(glassesPillName('SNT GS5 MAX_9475'), 'GS5 MAX');
    expect(glassesPillName('SANVNET GS4 MAX_2BAB'), 'GS4 MAX');
    expect(glassesPillName('gs4_1234'), 'GS4');
  });

  test('no name, no label', () {
    expect(glassesPillName(null), isNull);
    expect(glassesPillName('  '), isNull);
  });
}
