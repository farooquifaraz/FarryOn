import 'package:flutter/material.dart';

import '../../core/theme.dart';
import '../../core/ui.dart';
import 'conversations_screen.dart';
import 'notes_screen.dart';
import 'reminders_screen.dart';

/// A small hub that fans out to the three personal-data screens: Notes,
/// Reminders, and Conversations. Reachable from the live screen's top bar and
/// the Settings "Your stuff" section.
class YourStuffScreen extends StatelessWidget {
  const YourStuffScreen({super.key});

  static Future<void> open(BuildContext context) => Navigator.of(context).push(
        MaterialPageRoute<void>(builder: (_) => const YourStuffScreen()),
      );

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Aurora.base,
      appBar: AppBar(title: const Text('Your stuff')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
        children: [
          SettingsGroup(children: [
            SettingsRow(
              icon: Icons.sticky_note_2_rounded,
              gradient: Aurora.gradGreen,
              title: 'Notes',
              subtitle: 'Things Farry remembered for you',
              onTap: () => NotesScreen.open(context),
            ),
            SettingsRow(
              icon: Icons.alarm_rounded,
              gradient: Aurora.gradAmber,
              title: 'Reminders',
              subtitle: 'Time-based reminders & tasks',
              onTap: () => RemindersScreen.open(context),
            ),
            SettingsRow(
              icon: Icons.forum_rounded,
              gradient: Aurora.gradPurple,
              title: 'Conversations',
              subtitle: 'Read your past chats',
              onTap: () => ConversationsScreen.open(context),
              showDivider: false,
            ),
          ]),
        ],
      ),
    );
  }
}
