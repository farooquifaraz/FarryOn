/// One place that turns a price in minor units into what a person reads.
///
/// The backend sends every price as an integer in the currency's own minor
/// unit — cents for USD, paise for INR — plus the currency code. The screens
/// used to hard-code `'$'` and two decimals, which read "$599.00" for an
/// Indian plan the moment one existed. Rupees are shown whole, with Indian
/// grouping (₹5,500, ₹11,000); dirhams whole unless there are fils; dollars
/// keep cents as before; anything else gets its code.
String formatMoney(int minor, String currency) {
  final code = currency.toUpperCase();
  switch (code) {
    case 'USD':
      return '\$${(minor / 100).toStringAsFixed(2)}';
    case 'INR':
      // Rounded, not truncated: a yearly price divided by twelve
      // (₹11,000 → 91,666.67 paise) must read ₹917 as the website says.
      return '₹${_groupIndian((minor / 100).round())}';
    case 'AED':
      // Whole dirhams when there are no fils ("AED 25"), else two places
      // ("AED 14.17") — the UAE plans are whole, a twelfth of one is not.
      return minor % 100 == 0
          ? 'AED ${minor ~/ 100}'
          : 'AED ${(minor / 100).toStringAsFixed(2)}';
    default:
      return '$code ${(minor / 100).toStringAsFixed(2)}';
  }
}

/// 1234567 -> "12,34,567" (the last three digits, then pairs).
String _groupIndian(int n) {
  final s = n.abs().toString();
  if (s.length <= 3) return n < 0 ? '-$s' : s;
  final last3 = s.substring(s.length - 3);
  var rest = s.substring(0, s.length - 3);
  final parts = <String>[];
  while (rest.length > 2) {
    parts.insert(0, rest.substring(rest.length - 2));
    rest = rest.substring(0, rest.length - 2);
  }
  if (rest.isNotEmpty) parts.insert(0, rest);
  final out = '${parts.join(',')},$last3';
  return n < 0 ? '-$out' : out;
}
