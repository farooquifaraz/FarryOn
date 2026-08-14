import 'package:flutter/material.dart';

import '../../core/theme.dart';
import 'translate_languages.dart';

/// Full-height language chooser with a search box.
///
/// A sheet of chips worked for a dozen languages and stops working at 78: the
/// only way to reach Vietnamese was to scroll past everything. Search matches
/// the native name, the English name and the code, so it can be found by
/// someone typing on an English keyboard *or* on a keyboard already set to that
/// language.
/// How the picker learns which languages the phone can say.
///
/// Injected so a widget test does not need a speech engine.
typedef InstalledVoices = Future<Set<String>> Function();

class TranslateLanguagePicker extends StatefulWidget {
  const TranslateLanguagePicker({
    super.key,
    required this.selected,
    this.installedVoices,
    this.onAddVoices,
  });

  /// Currently chosen BCP-47 code, or empty when nothing has been picked.
  final String selected;

  /// Which languages this phone can speak. Null means do not ask, and nothing
  /// is marked — better than marking everything wrongly.
  final InstalledVoices? installedVoices;

  /// Opens the phone's voice installer. Null hides the offer.
  final Future<bool> Function()? onAddVoices;

  /// Returns the chosen code, or null if the user backed out.
  static Future<String?> open(
    BuildContext context,
    String selected, {
    InstalledVoices? installedVoices,
    Future<bool> Function()? onAddVoices,
  }) =>
      Navigator.of(context).push<String>(MaterialPageRoute<String>(
        builder: (_) => TranslateLanguagePicker(
          selected: selected,
          installedVoices: installedVoices,
          onAddVoices: onAddVoices,
        ),
      ));

  @override
  State<TranslateLanguagePicker> createState() =>
      _TranslateLanguagePickerState();
}

class _TranslateLanguagePickerState extends State<TranslateLanguagePicker> {
  final _search = TextEditingController();
  String _query = '';

  /// Base codes the phone can say. Empty until asked, and left empty when the
  /// engine cannot answer — an unknown answer must not mark every language as
  /// silent.
  Set<String> _voices = const {};

  @override
  void initState() {
    super.initState();
    final ask = widget.installedVoices;
    if (ask == null) return;
    ask().then((codes) {
      if (mounted) setState(() => _voices = codes);
    });
  }

  /// True when we KNOW this phone cannot speak the language. Unknown is not
  /// silent: with no answer from the engine, nothing is marked.
  Future<void> _addVoices() async {
    final opened = await widget.onAddVoices?.call() ?? false;
    if (opened || !mounted) return;
    // Some phones have no such screen. Say where to go rather than leaving a
    // button that silently does nothing.
    ScaffoldMessenger.of(context)
      ..clearSnackBars()
      ..showSnackBar(const SnackBar(
        content: Text(
          "Couldn't open it — try Android Settings, then search for "
          '"Text-to-speech".',
        ),
      ));
  }

  bool _silent(String code) =>
      _voices.isNotEmpty &&
      !_voices.contains(code.split(RegExp('[-_]')).first.toLowerCase());

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final matches =
        kTranslateLanguages.where((l) => l.matches(_query)).toList();

    return Scaffold(
      backgroundColor: Aurora.base,
      appBar: AppBar(
        backgroundColor: Aurora.base,
        elevation: 0,
        iconTheme: const IconThemeData(color: Aurora.textPrimary),
        title: const Text('Translate into',
            style: TextStyle(color: Aurora.textPrimary, fontSize: 17)),
      ),
      body: SafeArea(
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 4, 12, 10),
              child: TextField(
                controller: _search,
                autofocus: false,
                style: const TextStyle(
                    color: Aurora.textPrimary, fontSize: 15),
                cursorColor: Aurora.mint,
                onChanged: (v) => setState(() => _query = v),
                decoration: InputDecoration(
                  hintText: 'Search language',
                  hintStyle: const TextStyle(
                      color: Aurora.textMuted, fontSize: 15),
                  prefixIcon:
                      const Icon(Icons.search, color: Aurora.textMuted, size: 20),
                  suffixIcon: _query.isEmpty
                      ? null
                      : IconButton(
                          icon: const Icon(Icons.close,
                              color: Aurora.textMuted, size: 18),
                          onPressed: () {
                            _search.clear();
                            setState(() => _query = '');
                          },
                        ),
                  filled: true,
                  fillColor: Aurora.surface,
                  contentPadding:
                      const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: BorderSide.none,
                  ),
                ),
              ),
            ),
            const Padding(
              padding: EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  // Said once, here, so nobody goes looking for a setting that
                  // does not exist.
                  "You only choose what you want to hear — the speaker's "
                  'language is detected on its own.',
                  style: TextStyle(
                      color: Aurora.textMuted, fontSize: 12, height: 1.4),
                ),
              ),
            ),
            // Offered only once we KNOW something is missing — a phone that
            // can say everything should not be nagged, and one we could not
            // ask must not be either.
            if (widget.onAddVoices != null && _voices.isNotEmpty)
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
                child: Row(
                  children: [
                    const Icon(Icons.volume_off_outlined,
                        color: Aurora.textMuted, size: 16),
                    const SizedBox(width: 8),
                    const Expanded(
                      child: Text(
                        'Languages marked with this are shown but not spoken — '
                        'this phone has no voice for them yet.',
                        style: TextStyle(
                            color: Aurora.textMuted, fontSize: 11, height: 1.4),
                      ),
                    ),
                    TextButton(
                      onPressed: _addVoices,
                      child: const Text('Add voices',
                          style: TextStyle(color: Aurora.mint, fontSize: 12)),
                    ),
                  ],
                ),
              ),
            Expanded(
              child: matches.isEmpty
                  ? const Center(
                      child: Text(
                        'No language matches that.',
                        style:
                            TextStyle(color: Aurora.textMuted, fontSize: 13),
                      ),
                    )
                  : ListView.separated(
                      padding: const EdgeInsets.only(bottom: 16),
                      itemCount: matches.length,
                      separatorBuilder: (_, __) => const Divider(
                        height: 1,
                        thickness: 0.5,
                        color: Aurora.glassBorder,
                        indent: 16,
                        endIndent: 16,
                      ),
                      itemBuilder: (_, i) {
                        final l = matches[i];
                        final chosen = l.code == widget.selected;
                        return ListTile(
                          onTap: () => Navigator.pop(context, l.code),
                          title: Text(
                            l.native,
                            style: TextStyle(
                              color: chosen
                                  ? Aurora.mint
                                  : Aurora.textPrimary,
                              fontSize: 16,
                            ),
                          ),
                          // The English name is the second line rather than the
                          // first: someone looking for their own language finds
                          // it faster written the way they write it.
                          subtitle: l.native == l.name
                              ? null
                              : Text(
                                  l.name,
                                  style: const TextStyle(
                                      color: Aurora.textMuted, fontSize: 12),
                                ),
                          // Say up front whether this phone can SPEAK the
                          // language, not halfway through a conversation.
                          // Translation is voiced by the phone's own engine,
                          // and a phone only has the voices someone installed.
                          trailing: chosen
                              ? const Icon(Icons.check,
                                  color: Aurora.mint, size: 20)
                              : (_silent(l.code)
                                  ? const Icon(Icons.volume_off_outlined,
                                      color: Aurora.textMuted, size: 18)
                                  : null),
                        );
                      },
                    ),
            ),
          ],
        ),
      ),
    );
  }
}
