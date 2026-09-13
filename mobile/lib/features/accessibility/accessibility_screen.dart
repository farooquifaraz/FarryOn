import 'package:flutter/material.dart';

import '../../core/theme.dart';
import '../../core/ui.dart';
import '../translate/translate_screen.dart';
import 'speak_for_me_screen.dart';

/// The one place the modes for people of determination are found.
///
/// Nothing here is a "mode" on the server: each row is a screen the phone
/// opens with the right session set-up. See `docs/ACCESSIBILITY_DESIGN.md`
/// for what each one is, what it reuses, and what is still to come.
class AccessibilityScreen extends StatelessWidget {
  const AccessibilityScreen({super.key});

  static Future<void> open(BuildContext context) =>
      Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) => const AccessibilityScreen(),
      ));

  @override
  Widget build(BuildContext context) => Scaffold(
        backgroundColor: Aurora.base,
        appBar: AppBar(title: const Text('Accessibility')),
        body: ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 28),
          children: [
            const Padding(
              padding: EdgeInsets.fromLTRB(4, 4, 4, 18),
              child: Text(
                'FarryOn for people of determination. Pick the way you want '
                'to use it; Farry stays quiet while these screens are open '
                'and comes back when you leave.',
                style: TextStyle(
                    color: Aurora.textMuted, fontSize: 13, height: 1.5),
              ),
            ),
            const SectionLabel('Hearing'),
            SettingsGroup(children: [
              SettingsRow(
                icon: Icons.closed_caption_rounded,
                gradient: Aurora.gradTeal,
                title: 'Live captions',
                subtitle: 'Read what people around you say, in your language',
                onTap: () => TranslateScreen.open(context, captions: true),
                showDivider: false,
              ),
            ]),
            const SizedBox(height: 20),
            const SectionLabel('Speech'),
            SettingsGroup(children: [
              SettingsRow(
                icon: Icons.record_voice_over_rounded,
                gradient: Aurora.gradPurple,
                title: 'Speak for me',
                subtitle:
                    'Type it — the phone, or the glasses on someone, says it',
                onTap: () => SpeakForMeScreen.open(context),
                showDivider: false,
              ),
            ]),
            const SizedBox(height: 20),
            const SectionLabel('Sight'),
            SettingsGroup(children: [
              SettingsRow(
                icon: Icons.explore_rounded,
                gradient: Aurora.gradAmber,
                title: 'Guide',
                subtitle: 'What is ahead, read aloud — coming next',
                onTap: () => _GuidePreview.show(context),
                showDivider: false,
              ),
            ]),
          ],
        ),
      );
}

/// What Guide will be, and what Farry can already do meanwhile. Shown instead
/// of a dead button: a row that does nothing reads as broken.
class _GuidePreview extends StatelessWidget {
  const _GuidePreview();

  static Future<void> show(BuildContext context) => showModalBottomSheet<void>(
        context: context,
        backgroundColor: Aurora.surfaceHigh,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
        ),
        builder: (_) => const _GuidePreview(),
      );

  @override
  Widget build(BuildContext context) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 18, 20, 20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Guide',
                  style: TextStyle(
                      color: Aurora.textPrimary,
                      fontSize: 18,
                      fontWeight: FontWeight.w600)),
              const SizedBox(height: 10),
              const Text(
                'Guide will say what is in front of you as you walk, read '
                'signs and labels, and warn about steps, kerbs and obstacles '
                '— short, and in the order that matters. It is being built '
                'and tested on real routes before it is switched on.\n\n'
                'Until then, Farry can already describe what the camera sees: '
                'on the main screen, ask "What\'s in front of me?" or '
                '"Read this to me".',
                style: TextStyle(
                    color: Aurora.textMuted, fontSize: 13.5, height: 1.5),
              ),
              const SizedBox(height: 18),
              SizedBox(
                width: double.infinity,
                child: FilledButton(
                  style: FilledButton.styleFrom(
                    backgroundColor: Aurora.teal,
                    foregroundColor: Aurora.tealInk,
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(14)),
                  ),
                  onPressed: () =>
                      Navigator.of(context).popUntil((r) => r.isFirst),
                  child: const Text('Ask Farry'),
                ),
              ),
            ],
          ),
        ),
      );
}
