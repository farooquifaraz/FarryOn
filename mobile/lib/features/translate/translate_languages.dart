/// Every language live translation can translate INTO.
///
/// Taken verbatim from Google's supported-languages table for
/// `gemini-3.5-live-translate-preview` (checked 2026-08-10), because a code the
/// model does not know is silently replaced with English — which looks exactly
/// like the picker doing nothing.
///
/// **No region variants.** Vendor apps show lists like "Arabic (Lebanon)",
/// "Arabic (Qatar)", "Arabic (Morocco)". This model does not distinguish them:
/// it has one `ar`. Offering the choice would let someone pick Qatari Arabic
/// and receive plain Arabic, with nothing on screen to say why. The only
/// variants the model really has are Portuguese (`pt-BR` / `pt-PT`) and
/// Chinese (`zh-Hans` / `zh-Hant`), and those are listed as themselves.
///
/// The source language is never in this list — it is detected.
library;

/// A language the model can translate into.
class TranslateLanguage {
  const TranslateLanguage(this.code, this.name, this.native);

  /// BCP-47 code sent as `targetLanguageCode`.
  final String code;

  /// English name, for searching in a keyboard the user already has.
  final String name;

  /// The name in its own script, which is what the list shows first.
  final String native;

  /// Whether this language matches a search box query.
  ///
  /// Matches the native name, the English name and the code, so someone can
  /// find Hindi by typing "hi", "Hindi" or "हिन्दी" — the last of which
  /// matters when the phone keyboard is already in that language.
  ///
  /// Short queries match only at the START of a word. Plain substring matching
  /// meant "hi" also returned Swahili, whose native name is *Kiswahili* — and
  /// two letters is exactly what someone types when they know their language
  /// is near the top.
  bool matches(String query) {
    final q = query.trim().toLowerCase();
    if (q.isEmpty) return true;
    if (code.toLowerCase().startsWith(q)) return true;

    bool startsAWord(String haystack) => haystack
        .toLowerCase()
        .split(RegExp(r'[\s()\-]+'))
        .any((word) => word.startsWith(q));

    if (startsAWord(name) || startsAWord(native)) return true;
    // Only once the query is specific enough to be worth a loose match.
    if (q.length < 3) return false;
    return name.toLowerCase().contains(q) || native.toLowerCase().contains(q);
  }
}

/// The full set, ordered so the languages this product is actually used in come
/// first, then everything else alphabetically by English name.
const List<TranslateLanguage> kTranslateLanguages = [
  // Most used here — ahead of the alphabet on purpose, since a picker that
  // opens on "Afrikaans" makes everyone scroll.
  TranslateLanguage('hi', 'Hindi', 'हिन्दी'),
  TranslateLanguage('en', 'English', 'English'),
  TranslateLanguage('ur', 'Urdu', 'اردو'),
  TranslateLanguage('ar', 'Arabic', 'العربية'),

  TranslateLanguage('af', 'Afrikaans', 'Afrikaans'),
  TranslateLanguage('ak', 'Akan', 'Akan'),
  TranslateLanguage('sq', 'Albanian', 'Shqip'),
  TranslateLanguage('am', 'Amharic', 'አማርኛ'),
  TranslateLanguage('hy', 'Armenian', 'Հայերեն'),
  TranslateLanguage('az', 'Azerbaijani', 'Azərbaycan'),
  TranslateLanguage('eu', 'Basque', 'Euskara'),
  TranslateLanguage('be', 'Belarusian', 'Беларуская'),
  TranslateLanguage('bn', 'Bengali', 'বাংলা'),
  TranslateLanguage('bg', 'Bulgarian', 'Български'),
  TranslateLanguage('my', 'Burmese', 'မြန်မာ'),
  TranslateLanguage('ca', 'Catalan', 'Català'),
  TranslateLanguage('zh-Hans', 'Chinese (Simplified)', '简体中文'),
  TranslateLanguage('zh-Hant', 'Chinese (Traditional)', '繁體中文'),
  TranslateLanguage('hr', 'Croatian', 'Hrvatski'),
  TranslateLanguage('cs', 'Czech', 'Čeština'),
  TranslateLanguage('da', 'Danish', 'Dansk'),
  TranslateLanguage('nl', 'Dutch', 'Nederlands'),
  TranslateLanguage('et', 'Estonian', 'Eesti'),
  TranslateLanguage('fil', 'Filipino', 'Filipino'),
  TranslateLanguage('fi', 'Finnish', 'Suomi'),
  TranslateLanguage('fr', 'French', 'Français'),
  TranslateLanguage('gl', 'Galician', 'Galego'),
  TranslateLanguage('ka', 'Georgian', 'ქართული'),
  TranslateLanguage('de', 'German', 'Deutsch'),
  TranslateLanguage('el', 'Greek', 'Ελληνικά'),
  TranslateLanguage('gu', 'Gujarati', 'ગુજરાતી'),
  TranslateLanguage('ha', 'Hausa', 'Hausa'),
  TranslateLanguage('he', 'Hebrew', 'עברית'),
  TranslateLanguage('hu', 'Hungarian', 'Magyar'),
  TranslateLanguage('is', 'Icelandic', 'Íslenska'),
  TranslateLanguage('id', 'Indonesian', 'Indonesia'),
  TranslateLanguage('it', 'Italian', 'Italiano'),
  TranslateLanguage('ja', 'Japanese', '日本語'),
  TranslateLanguage('jv', 'Javanese', 'Basa Jawa'),
  TranslateLanguage('kn', 'Kannada', 'ಕನ್ನಡ'),
  TranslateLanguage('kk', 'Kazakh', 'Қазақ'),
  TranslateLanguage('km', 'Khmer', 'ខ្មែរ'),
  TranslateLanguage('rw', 'Kinyarwanda', 'Kinyarwanda'),
  TranslateLanguage('ko', 'Korean', '한국어'),
  TranslateLanguage('lo', 'Lao', 'ລາວ'),
  TranslateLanguage('lv', 'Latvian', 'Latviešu'),
  TranslateLanguage('lt', 'Lithuanian', 'Lietuvių'),
  TranslateLanguage('mk', 'Macedonian', 'Македонски'),
  TranslateLanguage('ms', 'Malay', 'Bahasa Melayu'),
  TranslateLanguage('ml', 'Malayalam', 'മലയാളം'),
  TranslateLanguage('mr', 'Marathi', 'मराठी'),
  TranslateLanguage('mn', 'Mongolian', 'Монгол'),
  TranslateLanguage('ne', 'Nepali', 'नेपाली'),
  TranslateLanguage('nb', 'Norwegian', 'Norsk'),
  TranslateLanguage('fa', 'Persian', 'فارسی'),
  TranslateLanguage('pl', 'Polish', 'Polski'),
  TranslateLanguage('pt-BR', 'Portuguese (Brazil)', 'Português (Brasil)'),
  TranslateLanguage('pt-PT', 'Portuguese (Portugal)', 'Português (Portugal)'),
  TranslateLanguage('pa', 'Punjabi', 'ਪੰਜਾਬੀ'),
  TranslateLanguage('ro', 'Romanian', 'Română'),
  TranslateLanguage('ru', 'Russian', 'Русский'),
  TranslateLanguage('sr', 'Serbian', 'Српски'),
  TranslateLanguage('sd', 'Sindhi', 'سنڌي'),
  TranslateLanguage('si', 'Sinhala', 'සිංහල'),
  TranslateLanguage('sk', 'Slovak', 'Slovenčina'),
  TranslateLanguage('sl', 'Slovenian', 'Slovenščina'),
  TranslateLanguage('es', 'Spanish', 'Español'),
  TranslateLanguage('su', 'Sundanese', 'Basa Sunda'),
  TranslateLanguage('sw', 'Swahili', 'Kiswahili'),
  TranslateLanguage('sv', 'Swedish', 'Svenska'),
  TranslateLanguage('ta', 'Tamil', 'தமிழ்'),
  TranslateLanguage('te', 'Telugu', 'తెలుగు'),
  TranslateLanguage('th', 'Thai', 'ไทย'),
  TranslateLanguage('tr', 'Turkish', 'Türkçe'),
  TranslateLanguage('uk', 'Ukrainian', 'Українська'),
  TranslateLanguage('uz', 'Uzbek', 'Oʻzbek'),
  TranslateLanguage('vi', 'Vietnamese', 'Tiếng Việt'),
  TranslateLanguage('zu', 'Zulu', 'isiZulu'),
];

/// Native name for a code, falling back to the code itself so an unknown one
/// shows as something rather than as a blank.
String translateLanguageName(String code) {
  for (final l in kTranslateLanguages) {
    if (l.code == code) return l.native;
  }
  return code;
}

/// Languages written right-to-left.
///
/// Rendering these left-aligned is what made a perfectly good Urdu translation
/// look wrong: the words were right, the line simply started on the wrong side
/// and the full stop landed at the wrong end. Flutter takes its direction from
/// the ambient `Directionality`, which the app sets to LTR, so RTL text has to
/// be asked for explicitly.
const Set<String> _kRtlCodes = {'ar', 'ur', 'fa', 'he', 'sd'};

/// Whether text in [code] should be laid out right-to-left.
bool isRtlLanguage(String? code) =>
    code != null && _kRtlCodes.contains(code.split('-').first.toLowerCase());
