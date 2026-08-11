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
class TranslateLanguagePicker extends StatefulWidget {
  const TranslateLanguagePicker({super.key, required this.selected});

  /// Currently chosen BCP-47 code, or empty when nothing has been picked.
  final String selected;

  /// Returns the chosen code, or null if the user backed out.
  static Future<String?> open(BuildContext context, String selected) =>
      Navigator.of(context).push<String>(MaterialPageRoute<String>(
        builder: (_) => TranslateLanguagePicker(selected: selected),
      ));

  @override
  State<TranslateLanguagePicker> createState() =>
      _TranslateLanguagePickerState();
}

class _TranslateLanguagePickerState extends State<TranslateLanguagePicker> {
  final _search = TextEditingController();
  String _query = '';

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
                          trailing: chosen
                              ? const Icon(Icons.check,
                                  color: Aurora.mint, size: 20)
                              : null,
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
