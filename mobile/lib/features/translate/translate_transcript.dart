/// Turning a live translation into something worth keeping.
///
/// The on-screen transcript is a live view — provisional lines, a capped
/// backlog, colours that mean something only while you are watching. A saved
/// copy is read later, by someone who was not there, possibly on another
/// device. So it is rendered rather than dumped.
library;

import 'translate_languages.dart';
import 'translate_state.dart';

/// Render [turns] as the note body to save.
///
/// * Unfinished lines are dropped. A half-heard sentence is noise in a record.
/// * The heard language is named in its own script, matching the screen.
/// * A line the model deliberately left alone is marked as such, not left
///   looking like a translation that failed to arrive.
String renderTranslationNote({
  required List<TranslateTurn> turns,
  required String targetLanguage,
  required DateTime at,
}) {
  final target = translateLanguageName(targetLanguage);
  final lines = <String>['Live translation → $target', _stamp(at), ''];

  for (final turn in turns) {
    final heard = turn.heard.trim();
    if (heard.isEmpty || !turn.heardFinal) continue;
    final lang = turn.heardLang;
    lines.add(lang == null
        ? heard
        : '[${translateLanguageName(lang)}] $heard');
    if (turn.sameLanguage) {
      lines.add('  (already in $target — nothing to translate)');
    } else {
      final translated = turn.translated.trim();
      if (translated.isNotEmpty) lines.add('  → $translated');
    }
    lines.add('');
  }

  return lines.join('\n').trimRight();
}

/// Whether there is anything worth saving yet — drives the button's enabled
/// state, so nobody taps Save and gets an empty note.
bool hasSomethingToSave(List<TranslateTurn> turns) =>
    turns.any((t) => t.heardFinal && t.heard.trim().isNotEmpty);

String _stamp(DateTime at) {
  const months = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];
  final h = at.hour % 12 == 0 ? 12 : at.hour % 12;
  final m = at.minute.toString().padLeft(2, '0');
  final ampm = at.hour < 12 ? 'am' : 'pm';
  return '${at.day} ${months[at.month - 1]} ${at.year}, $h:$m $ampm';
}
