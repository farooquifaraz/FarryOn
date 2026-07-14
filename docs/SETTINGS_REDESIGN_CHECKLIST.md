# Settings redesign — configurable options checklist

Purpose: redesign the Settings UI (Midnight Aurora + gradients + Material rounded
colorful icons) **without breaking any functionality**. Every option below must
stay wired to the exact same state/config as today. Backend is NOT touched.

Source of truth for current behaviour: `mobile/lib/features/live/live_screen.dart`
(`_SettingsSheet`, `_showDeviceSheet`) and `mobile/lib/core/config.dart`.

Save contract (must stay identical): writing `ref.read(configProvider.notifier).state = cfg`
triggers persist + reconnect via `LiveNotifier` (providers.dart:104-109). Device
picks go through `liveProvider.notifier.setAudioDevice/setVideoDevice`.

## A. Connection / server  → sub-page "Server"  ✅ (`_ServerPage`)
- [x] A1. Cloud vs Local one-tap preset (fills host/port/secure)  — `_useCloud`/`_useLocal`
- [x] A2. Host field  → `AppConfig.host`
- [x] A3. Port field  → `AppConfig.port`
- [x] A4. Secure (TLS) switch  → `AppConfig.secure`
- [x] A5. Live connection status pill (read-only, watches `liveProvider.connection`)

## B. Capture devices  → sub-page "Devices"  ✅ (`_DevicesPage`)
- [x] B1. Microphone: Phone/earbuds vs Glasses  → `setAudioDevice` (`state.audioKind`)
- [x] B2. Camera: Phone vs Glasses  → `setVideoDevice` (`state.videoKind`)

## C. Voice / mic  → sub-page "Voice & mic"  ✅ (`_VoiceMicPage`)
- [x] C1. Hands-free mic toggle  → `AppConfig.handsFree`

## D. AI provider  → sub-page "AI model"  ✅ (`_AiModelPage`)
- [x] D1. Provider: Gemini / OpenAI / Mock  → `AppConfig.provider`

## E. Email (own inbox)  → sub-page "Email inbox"  ✅ (`_EmailPage`)
- [x] E1. Provider preset: Gmail/Outlook/Yahoo/Hostinger/Custom  → `AppConfig.emailProvider`
- [x] E2. Email address  → `AppConfig.emailAddress`
- [x] E3. App password (show/hide toggle)  → `AppConfig.emailAppPassword`
- [x] E4. IMAP host (custom only)  → `AppConfig.emailImapHost`
- [x] E5. SMTP host (custom only)  → `AppConfig.emailSmtpHost`
- [x] E6. SMTP port (custom only)  → `AppConfig.emailSmtpPort`
- [x] E7. Host resolution logic preserved: custom → fields, preset → `EmailProviders.presets`

## F. Web search  → sub-page "Web search"  ✅ (`_WebSearchPage`)
- [x] F1. Provider: Tavily / Serper / SerpAPI  → `AppConfig.webSearchProvider`
- [x] F2. API key  → `AppConfig.webSearchApiKey`
- [x] F3. Fallback API key preserved  → `AppConfig.webSearchFallbackApiKey` (now a visible field)

## G. Diagnostics / navigation  → section "About"  ✅
- [x] G1. Debug logs  → `DebugLogsScreen.open`
- [x] G2. Glasses Lab (debug builds only, `kDebugMode`)  → existing open flow (callback)
- [x] G3. App version display

## H. Save  ✅
- [x] H1. Each sub-page saves its slice via `copyWith` (unshown fields preserved)
- [x] H2. Save still persists + reconnects (unchanged `configProvider` contract)

## Design (applies to all of the above)  ✅
- [x] Keep Midnight Aurora palette; add gradients to buttons + icon tiles + header
- [x] Material rounded icons everywhere, colorful gradient fill (ShaderMask via `GradientIcon`)
- [x] Grouped hub + focused sub-pages (no single long scroll)

Verified: `flutter analyze` on all 4 changed files → No issues found. Backend
untouched; all config wiring identical. NOT YET device-tested (see
test-locally-before-push).

## Phase 2 (separate screens)  ✅ (screens split)
- [x] Split Notes & Tasks 3-tab screen → separate Notes / Reminders / Conversations screens
      - `features/data/notes_screen.dart` (`NotesScreen`) — same DataApi.notes/deleteNote
      - `features/data/reminders_screen.dart` (`RemindersScreen`) — same DataApi.tasks/setTaskDone/deleteTask + grouping
      - `features/data/conversations_screen.dart` (`ConversationsScreen` + `ChatSessionScreen`) — same ChatHistoryStore
      - `features/data/your_stuff_screen.dart` (`YourStuffScreen`) — hub → the 3 screens
      - `features/data/data_common.dart` — shared date/group helpers + empty/error/card
      - old `notes_tasks_screen.dart` deleted; live-screen top bar → "Your stuff"; Settings hub → "Your stuff" section
- [x] Roll gradient icons across live screen controls + top bar
      - bottom controls (`_CircleButton`): camera=blue, flip=teal, glasses-shutter=green,
        scan=purple, interrupt=coral (danger); disabled stays flat muted grey
      - big mic button: teal→mint `primaryGradient` fill while listening
      - text-field send icon: teal gradient
      - top bar (`_BarIcon`): Finder=blue, Your stuff=green, orientation=teal, settings=purple
        (switched to `_rounded` glyphs); status chips left as-is (semantic colours)

Verified: `flutter analyze lib` → clean (only pre-existing media_saver info remains,
unrelated). NOT device-tested yet.
