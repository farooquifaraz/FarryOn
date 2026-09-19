import 'package:farryon/core/money.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('dollars keep cents, rupees are whole with Indian grouping', () {
    expect(formatMoney(1500, 'USD'), '\$15.00');
    expect(formatMoney(542, 'usd'), '\$5.42');
    expect(formatMoney(29900, 'INR'), '₹299');
    expect(formatMoney(550000, 'INR'), '₹5,500');
    expect(formatMoney(1100000, 'INR'), '₹11,000');
    expect(formatMoney(123456700, 'INR'), '₹12,34,567');
    expect(formatMoney(5500, 'AED'), 'AED 55.00');
    expect(formatMoney(100, 'GBP'), 'GBP 1.00');
  });
}
