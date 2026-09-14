# FarryOn — Smart Glasses Sample & SDK Requirements

**Models:** L801 (current), GS4 MAX, GS5 MAX
**Document:** RFQ-GLASSES-2026-09 · Version 1.0 · 14 September 2026
**From:** FarryOn · [your name, company, email, phone, WeChat]
**To:** [vendor company · sales contact · technical contact]

Priority tags used below: **MUST** = required before we place a bulk order · **SHOULD** = strongly preferred · **INFO** = please confirm in your reply.

---

## 1. Who we are and what we need

FarryOn is a real-time voice + vision AI assistant. The phone app (Android today, iOS next) is the hub; the glasses act as a hands-free microphone, speaker and first-person camera. All intelligence runs in our app and cloud, so the glasses need no AI of their own — only a stable SDK.

We have already integrated the **L801** using the HeyCyan Android SDK (LIB_GLASSES_SDK-release_3.aar, v1.0.2, 2025-08-16) and have hardware-verified BLE connection, battery and wear events, AI-photo + BLE thumbnail, WiFi-P2P media sync, glasses-mic PCM over BLE, HFP call-mode mic and A2DP TTS playback on firmware `AM01L2_2.00.00_260114`.

We are now choosing the model for our next batch. Please supply **evaluation samples plus the latest SDK** for all three models so we can run our acceptance tests and select one. Target bulk quantity: [e.g. 500 units in Q4 2026, then 2,000+].

---

## 2. Sample request (MUST)

| Model | Qty | Variant | Accessories to include | Notes |
| --- | --- | --- | --- | --- |
| L801 | 2 | Black, 1× sunglass + 1× gradient lens | Magnetic charging cable, spare nose pads, box | Ship with the **latest** firmware; state the exact version on the box |
| GS4 MAX | 2 | Black, dual lens | Magnetic charging cable, spare nose pads, box | State firmware version and BLE advertising name |
| GS5 MAX | 2 | Black, dual lens | **Charging case**, Type-C cable, spare nose pads, box | State firmware version and BLE advertising name |

For every sample please provide: serial number, firmware version (BT chip and WiFi chip), hardware revision, and the BLE advertising name as printed by the device.

Shipping: DDP to [city, country], courier with tracking. Please quote sample price and confirm that sample cost is credited against the first bulk order.

---

## 3. Specification confirmation sheet (INFO)

Please fill in or correct every row for each model. Where a value is "same as L801", say so explicitly.

| Item | L801 | GS4 MAX | GS5 MAX |
| --- | --- | --- | --- |
| BLE advertising name format (e.g. `L801_XXXX`) | | | |
| Bluetooth main chip | JL7018F | JL7018F? | JL7018F6? |
| Co-processor (camera/WiFi) | | Allwinner V821L2? | Allwinner V821? |
| Camera sensor | Sony IMX219, 8 MP | | |
| Photo resolution (pixels) | 6560 × 4928 measured | | |
| Video resolution / fps / codec | 1080p | 4K? fps? | 4K? fps? |
| Electronic image stabilisation | Yes | | |
| Storage (built-in / max) | 4 GB / 32 GB | 4 GB / 32 GB? | |
| Battery (mAh) and runtime: audio / camera / standby | 270 mAh · ~8 h audio | 270 mAh | 290 mAh |
| Charging case (capacity, charge cycles for glasses) | — | — | 3000 or 3600 mAh? |
| Charging method and full-charge time | Magnetic, ~60 min | | |
| Ingress rating | IP54 | IP65 | IP67 |
| Weight (g) | 41.8 | | |
| Microphones (count) and noise cancellation (ENC) | Dual mic | Dual mic ENC | |
| Speaker type (open-ear / bone conduction) | | | |
| Bluetooth version and profiles (BLE, A2DP, HFP, mSBC wideband) | BT 5.4 | | |
| WiFi (P2P / AP mode, band, measured throughput) | P2P, ~4.2 MB/s measured | | |
| Wear-detection sensor | Yes | | |
| Touch / gesture inputs (list every gesture) | Long-press, slide, tap, double tap | | |
| Indicator LED / photo LED | | | |
| Phone OS compatibility | Android 6.0+, iOS 10.0+ | | |
| Firmware version shipped on samples | | | |
| Lens options (Rx / photochromic / polarised) | | | |

---

## 4. SDK and software requirements

### 4.1 Deliverables (MUST)

1. **Latest Android SDK** (.aar) with version number and build date — we currently hold v1.0.2 (2025-08-16) and have seen references to v1.2.5 (aar 20260709_8). Please supply the newest release.
2. **Latest iOS SDK** (QCSDK.framework or successor) with version number and build date.
3. **English documentation** (PDF or HTML) for both platforms, including the full BLE command/notification table (command bytes, notify codes, payload layouts).
4. **Sample app source code** for Android (Kotlin/Java) and iOS (Swift/Objective-C) that builds and runs against the supplied SDK.
5. **Changelog** between v1.0.2 and the latest version, and the roadmap for the next 6–12 months.
6. **Supported-model list** with the numeric `glassesModel` ID returned by the SDK for each model (L801 returns 21).
7. **OTA firmware files** for each model with release notes, and the procedure to flash them through the SDK.
8. **SDK licence text** (see 4.8).

### 4.2 Compatibility matrix (MUST)

Confirm in writing:

- Does **one** Android .aar and **one** iOS framework support L801, GS4 MAX and GS5 MAX? If not, which SDK version supports which model?
- Are the BLE command set, notify codes and payload layouts identical across the three models? List every difference.
- Minimum firmware version per model for full SDK functionality.

### 4.3 Functional checklist (MUST — confirm per model)

| Function | SDK API we use today | L801 | GS4 MAX | GS5 MAX |
| --- | --- | --- | --- | --- |
| BLE scan, direct connect by MAC, auto-reconnect | `BleScannerHelper`, `connectDirectly`, `setReConnectMac`, `setNeedConnect` | ✓ verified | | |
| Battery level + charging state (notify 0x05) | `syncBattery` | ✓ | | |
| Device info (BT + WiFi firmware/hardware) | `syncDeviceInfo` | ✓ (WiFi fields empty) | | |
| Wear on/off events (notify 0x09) | `wearCheck`, `wearFunctionSupport` | ✓ | | |
| AI photo command + BLE JPEG thumbnail | `glassesControl(0x02,0x01,0x06)`, `getPictureThumbnails(size)` | ✓ 3.8 s median | | |
| Thumbnail size table (0x00–0x06 → pixels / bytes) | — | 0x02 = 512×384 | | |
| WiFi-P2P full-resolution media sync | `importAlbum`, `WifiFilesDownloadListener` | ✓ | | |
| Video record start/stop, duration config, busy codes | `glassesControl(0x02/0x03)` | ✓ (err=255 when busy) | | |
| Audio memo record + `recordingToPcm` | `glassesControl(0x08/0x0c)` | ✓ | | |
| Volume control (music / call / system) | `getVolumeControl`, `setVolumeControl` | ✓ | | |
| Time sync, unbind, OTA progress | `syncTime`, `unBindDevice`, notify 0x04 | ✓ | | |
| Storage-full event (0x0e) and delete-file command | — | ✓ | | |

### 4.4 Audio (MUST — answer every line)

1. **Push-to-talk PCM** (`voiceFromGlasses`): sample rate, bit depth, channels, codec on the wire (Opus?). L801 measured 16 kHz / 16-bit / mono.
2. **Continuous hands-free mic** (`AudioSoundControl.startTwsStereoCapture`, `soundControl` command 0x41): which models and firmware versions support it? L801 firmware `AM01L2_2.00.00_260114` answers `ffff` (unknown command). Provide firmware that supports it, if it exists.
3. **Remote mic open** (`aiVoiceWake`): does any model let the app open the glasses mic without a touch gesture?
4. **Noise cancellation / ENC**: is it applied (a) on the HFP call path, (b) on the BLE PCM path, (c) on the hands-free Opus path? Which algorithm, and is there an SDK toggle or level setting?
5. **HFP**: wideband (mSBC, 16 kHz) supported? Which HFP version?
6. **A2DP**: codecs supported (SBC, AAC, others). Latency for TTS playback.
7. **Coexistence**: can BLE data (thumbnail transfer) run while the HFP/SCO link is open? We measured BLE stalls with SCO up.
8. **Speaker**: max SPL, and whether volume set via SDK persists across power cycles.

### 4.5 Vision (MUST)

1. **Real-time preview / live stream** (`realTimePreview(url)` in SDK 1.2.5): which models and firmware support it? Protocol (RTSP over WiFi-AP or P2P?), resolution, frame rate, end-to-end latency, power draw, and whether BLE commands still work during streaming.
2. AI-photo capture latency (command → image stored) per model.
3. Thumbnail transfer speed over BLE (kB/s) and the MTU negotiated.
4. Full-resolution photo dimensions and average JPEG size per model.
5. Field of view of the camera and whether the lens is user-cleanable/replaceable.

### 4.6 Gestures and wake word (SHOULD)

1. Full list of gesture events reported to the app, with notify codes. On L801 only long-press (0x03), volume slide (0x12) and pause (0x0c) reach the app; single/double taps do not.
2. Can tap events be reported to the app in a firmware update?
3. Can gesture mapping be changed by the app?
4. Custom wake word (e.g. "Hey Farry") on the glasses: possible? Cost and lead time?

### 4.7 Firmware behaviours to confirm (INFO)

We observed the following on L801 and need to know if they persist on GS4 MAX / GS5 MAX:

- WiFi-P2P does not come up while the glasses are charging.
- A P2P session can go stale after an interrupted transfer and needs a reset command.
- Video start refused with err=255 while the glasses are busy; no retry hint.
- The AI-photo capture notify (0x02) occasionally fires twice, stalling the thumbnail transfer.
- Media counts update lazily (still stale ~1.5 s after a sync completes).
- Service-discovered is re-broadcast every ~2.5 s on a live link; repeated `initEnable` calls stack listeners.

### 4.8 Licence, data and privacy (MUST)

1. Written permission to embed and redistribute the SDK inside our own branded app on Google Play and the App Store, with **no per-device royalty** and no HeyCyan branding requirement.
2. Confirmation that end users do **not** need the HeyCyan app or a HeyCyan account.
3. Does the SDK make **any** network call to vendor or third-party servers (analytics, OTA check, cloud AI)? List every endpoint. We require zero outbound calls except the OTA download we trigger.
4. Any open-source components inside the SDK and their licences.
5. Right to receive security fixes for the SDK for at least 24 months.

### 4.9 Technical support (MUST)

- A named SDK engineer as our technical contact (email + WeChat or similar).
- Response time ≤ 2 business days for technical questions; ≤ 5 business days for a bug fix estimate.
- SDK and firmware update cadence, and a minimum 24-month support window for the models we buy.

---

## 5. Customisation and white-label (SHOULD)

For each item: possible yes/no, MOQ, one-time cost, unit cost, lead time.

1. Custom BLE advertising name prefix (e.g. `FARRY_XXXX`).
2. Custom voice prompts / power-on sound.
3. Custom wake word on the glasses.
4. Logo on the temple (laser or print) and custom retail packaging.
5. Custom firmware defaults (e.g. wear detection on, thumbnail size, video duration).
6. Prescription (Rx) lens support and lens replacement process.

---

## 6. Certifications and compliance (MUST)

Please provide copies of current test reports and certificates for each model:

- CE (RED), FCC, RoHS, REACH.
- Bluetooth SIG **BQB** listing (QDID).
- Battery: UN38.3, MSDS, IEC 62133; cell supplier and rated cycle life.
- Any country-specific approvals you already hold (e.g. BIS India, KC Korea, TELEC Japan).
- Eye-safety / photobiological statement if the device has an LED near the eye.

---

## 7. Warranty and after-sales (MUST)

| Item | Our requirement |
| --- | --- |
| Warranty period | Minimum 12 months from delivery on glasses and charging case |
| DOA (dead on arrival) | Replacement at vendor cost if reported within 30 days of receipt |
| Acceptable defect rate | ≤ 2% per batch; above that, credit or replacement for the whole batch |
| RMA process | Written procedure, turnaround ≤ 15 business days, freight responsibility stated |
| Spare parts | Nose pads, charging cables, charging cases, hinges available for 24 months |
| Battery | ≥ 500 charge cycles to 80% capacity; state the cell supplier |
| Firmware | Bug-fix firmware releases for at least 24 months after last shipment |
| Sample warranty | Samples covered by the same DOA terms |

---

## 8. Commercial terms (INFO)

Please quote for each model:

1. Sample price (per unit, with accessories) and whether it is credited against the bulk order.
2. Unit price at 100 / 500 / 1,000 / 5,000 units, FOB Shenzhen and DDP [destination].
3. MOQ per model and per colour.
4. Production lead time after deposit, and current stock.
5. Payment terms (e.g. 30% deposit, 70% before shipment) and accepted methods.
6. Packaging: retail box contents, master carton quantity, dimensions and weight.
7. Price validity period (we ask for 90 days).
8. Any exclusivity or ODM terms available for a custom-branded version.

---

## 9. Acceptance tests we will run on the samples (INFO)

We will run the following on each sample within 10 business days of receipt and share results. A model that fails a MUST item is not eligible for the bulk order unless a firmware or SDK fix is delivered.

1. BLE scan, direct connect, 20 auto-reconnect cycles, background survival.
2. Battery and wear events; charging behaviour with and without the case.
3. AI photo → BLE thumbnail latency (20 samples), full-res WiFi sync of 10 photos and 3 videos.
4. Mic paths: push-to-talk PCM, hands-free continuous mic, HFP call-mode; speech-to-text accuracy in a quiet room and at 60 dB background noise.
5. TTS playback via A2DP; volume persistence.
6. Live preview stream if supported: latency and stability over 10 minutes.
7. Video record 30 s / 4 min, busy handling, storage-full handling.
8. Battery life: continuous session with periodic photos until 0%.
9. OTA update using the supplied firmware file.

---

## 10. Questions to answer in your reply

1. Which SDK version (Android and iOS) supports all three models, and where can we download it?
2. What is the `glassesModel` ID and BLE name prefix for GS4 MAX and GS5 MAX?
3. Which models/firmware support continuous hands-free mic, remote mic open and real-time preview?
4. Is noise cancellation active on the BLE PCM path, and can the app control it?
5. Does the SDK contact any server? Which ones?
6. Can we ship our app without the HeyCyan app or account?
7. What are the warranty, DOA and RMA terms?
8. What is the earliest date samples can ship, and the price?

---

## 11. Reply format and timeline

Please reply within **5 business days** by returning this document with the tables filled in, plus the SDK package and certificates as attachments (or a download link). Mark anything not available with a date when it will be.

Contact for this request: [name · email · phone · WeChat]
