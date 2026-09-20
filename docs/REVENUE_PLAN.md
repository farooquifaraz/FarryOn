# FarryOn — Revenue, Cost & P&L Plan

> ⚠️ **2026-09-05 — नंबर बदल गए हैं, और अब वे मापे हुए हैं।** नीचे का पुराना विश्लेषण
> इतिहास के तौर पर रखा है; **चालू सच्चाई §0-A में है।**

---

## 0-A. VERIFIED P&L (2026-09-05)

### असली लागत — Google के bill से निकाली, अनुमान नहीं

Google Cloud → Billing → Reports → group by SKU (1–4 सितंबर 2026):

| SKU | Usage | ख़र्च | इससे निकली दर |
|---|---|---|---|
| BidiGenerateContent **text input** (2.5 Flash Native Audio) | 161,659 tokens | $0.08 | **$0.49 / 1M** |
| BidiGenerateContent **audio output** | 839 tokens | $0.01 | **$11.92 / 1M** |
| image input / audio input / flash text | ~1,400 | $0.00 | — |
| **कुल** | | **$0.09** | अगस्त पूरा महीना: **$3.83** |

अपने ही logs से मापा (35 sessions): **15,561 tokens प्रति live मिनट**, जिसमें **99.1% input**
(आवाज़ + हर turn पर दोबारा भेजा गया context) और सिर्फ़ 0.9% output।

> ### 🎯 **एक talk-मिनट = $0.0093**
> (15,421 input × $0.49/1M) + (140 output × $11.92/1M) ≈ **$0.0093**
>
> पुरानी मान्यता $0.012 थी — यानी **असली लागत 22% कम**। मान्यता सुरक्षित थी।

बाक़ी दरें: image scan **$0.003** · web search **$0.002** · infra/email **~$0.25/user/माह** ·
Stripe **2.9% + $0.30**।

**Translation की लागत = voice के बराबर** — वही live model सुनने के लिए इस्तेमाल होता है।
इसीलिए अब दोनों एक ही बजट से चलते हैं (देखें §0-B)।

---

## 0-B. चालू plans — global (USD), 2026-09-21 से

एक product, तीन price list (USD / AED / INR), **caps तीनों में एक जैसे**
(Faraz, 2026-09-21)। USD list UAE list से थोड़ी ऊपर ($5/8/15 बनाम AED 15/25/45 ≈
$4.08/6.81/12.25) — विदेशी card fee और currency risk के लिए। Yearly: Lite $50,
Plus $80, Pro $150 (≈2 महीने free)।

| | Free (trial) | Lite | Plus | Pro |
|---|---|---|---|---|
| **क़ीमत** | $0 | **$5**/माह | **$8**/माह | **$15**/माह |
| **Talk time** (voice + translation, एक बजट) | 30 मिनट *(lifetime)* | **120 मिनट/माह** | **250 मिनट/माह** | **400 मिनट/माह** |
| **Image scans** | 10 *(lifetime)* | 70/माह | 130/माह | 240/माह |
| **Web searches** | 15 *(lifetime)* | 110/माह | 225/माह | 400/माह |

पुराने (2026-09-05) global plans थे $6/15/25 पर 200/500/1000 मिनट; नीचे का P&L
उसी पर है और सिर्फ़ इतिहास के लिए रखा है — नए caps पर margin §0-D (UAE table)
जैसा ही बैठता है (100% use पर ~50%+)।

तीन ढाँचागत बदलाव:
1. **एक "talk time" बजट** — voice और translation की लागत बराबर है, तो अलग allowance रखना
   सिर्फ़ छेद था (पुराना Plus: 360 voice मिनट बनाम 1,350 translate मिनट — और महँगा हिस्सा
   वही था जिसकी क़ीमत किसी ने लगाई ही नहीं थी)।
2. **Monthly caps, daily नहीं** — user को आज़ादी, हमें पूरा हिसाब।
3. **"Unlimited" ख़त्म** — Pro के असीमित scans पर एक script $135/माह खा सकती थी।

### P&L

| Plan | कमाई | worst case *(हर cap पूरा)* | | वास्तविक *(talk 50%, बाक़ी 25%)* | |
|---|---|---|---|---|---|
| | | मुनाफ़ा | margin | मुनाफ़ा | margin |
| **Lite** | $6 | **+$2.57** | 43% | +$4.13 | 69% |
| **Plus** | $15 | **+$6.27** | 42% | +$10.91 | 73% |
| **Pro** | $25 | **+$9.43** | 38% | +$17.82 | 71% |

**Free trial की लागत: $0.34 प्रति signup** — एक बार, हर महीने नहीं।

पिछला ढाँचा worst case में **हर plan घाटे में** था: Lite −$5.87, Plus −$16.62, Pro −$168.31
(translate की खुली limit + Pro के unlimited scans)। अब **कोई भी हालत घाटे की नहीं**।

---

<details>
<summary><b>पुराना विश्लेषण (अगस्त 2026) — इतिहास के लिए</b></summary>


> **इस document का मकसद:** FarryOn का पूरा पैसा-हिसाब एक जगह, आसान भाषा में — कहाँ खर्च होता है,
> कहाँ से कमाई आती है, और हर पैमाने (100 → 10,000 users) पर profit/loss (P&L) क्या रहेगा।
> जो भी plan बदलना हो, सिर्फ़ `backend/app/config.py` के `plan_catalog` में बदलो — बाकी सब
> (daily caps, billing table) अपने-आप उसी से बनता है।

---

## 0-C. India price list (2026-09-20 से, config में set)

India से आने वाले (timezone/IP से पहचान — `core/region.py`) को USD की जगह ये दिखते
हैं; **one-time payment** (auto-renew नहीं — Indian cards पर विदेशी merchant का
recurring RBI e-mandate माँगता है), GST-inclusive, Stripe Checkout `payment` mode,
webhook पर 30/365 दिन की period; दोबारा खरीदने पर period आगे जुड़ता है; तारीख
निकलते ही free पर। Caps monthly, वही quota मशीनरी।

लागत: voice $0.0093/min (measured), scan $0.003, search $0.002, infra $0.25/user;
Stripe 3.5% + $0.30; GST 18% price के अंदर; ₹95.96/$।

| Plan | कीमत | मिनट / scan / search | हाथ में (GST+fee के बाद) | Cost @100% | Margin @100% | @60% use |
|---|---|---|---|---|---|---|
| साथी (`sathi_in`) | ₹299/माह | 120 / 70 / 110 | $2.25 | $1.80 | 20% | 48% |
| Plus (`plus_in`) | ₹599/माह | 250 / 130 / 225 | $4.80 | $3.42 | 29% | 55% |
| Pro (`pro_in`) | ₹999/माह | 400 / 240 / 400 | $8.21 | $5.49 | 33% | 59% |
| साथी yearly | ₹3,300/साल (1 माह free) | वही | $2.32/माह | $1.80 | 23% | 49% |
| Plus yearly | ₹5,500/साल (2.8 माह free) | वही | $3.88/माह | $3.42 | 12% | 45% |
| Pro yearly | ₹11,000/साल (1 माह free) | वही | $7.79/माह | $5.49 | 29% | 56% |

Breakeven minutes (scan+search पूरे use हों तब भी) हर plan में cap से ऊपर — कोई
plan किसी हालत में घाटे में नहीं। Plus yearly सबसे पतला (Faraz का चुनाव, promo)।

ध्यान: (1) Indian card पर "international" ON चाहिए, UPI/netbanking नहीं चलेगा
(foreign Stripe account); (2) OIDAR — विदेशी supplier को Indian GST registration —
CA से confirm; (3) card-country check अभी नहीं (Faraz: बाद में)।

## 0-D. UAE price list (2026-09-20 से, config में set)

UAE से आने वाले (timezone `Asia/Dubai` — सिर्फ़ UAE, बाकी Gulf global list) को
AED में ये दिखते हैं; **recurring** (auto-renew — UAE card पर कोई RBI जैसी रोक
नहीं), VAT-inclusive, Stripe Checkout `subscription` mode, बाकी सब global जैसा।
दाम Faraz के (15/25/45), caps **India वाले** — global caps (200/500/1000 मिनट) पर
Plus/Pro 100% use में घाटे में जाते (−33%) और UAE मिनट India से सस्ता पड़ता।

लागत: voice $0.0093/min, scan $0.003, search $0.002, infra $0.25/user;
Stripe UAE 2.9% + AED 1; VAT 5% price के अंदर; AED 3.6725/$; ₹26.12/AED।

| Plan | कीमत | मिनट / scan / search | हाथ में (VAT+fee के बाद) | Cost @100% | Margin @100% | @60% use | ₹/मिनट (India) |
|---|---|---|---|---|---|---|---|
| Lite (`lite_ae`) | AED 15/माह | 120 / 70 / 110 | $3.50 | $1.80 | 49% | 66% | 3.27 (2.49) |
| Plus (`plus_ae`) | AED 25/माह | 250 / 130 / 225 | $6.02 | $3.42 | 43% | 64% | 2.61 (2.40) |
| Pro (`pro_ae`) | AED 45/माह | 400 / 240 / 400 | $11.06 | $5.49 | 50% | 69% | 2.94 (2.50) |
| Lite yearly | AED 170/साल (0.7 माह free) | वही | $3.54/माह | $1.80 | 49% | 67% | 3.08 (2.29) |
| Plus yearly | AED 275/साल (1 माह free) | वही | $5.75/माह | $3.42 | 41% | 63% | 2.39 (1.83) |
| Pro yearly | AED 500/साल (0.9 माह free) | वही | $10.47/माह | $5.49 | 48% | 68% | 2.72 (2.29) |

Breakeven minutes हर plan में cap से 2× ऊपर। Stripe test-mode AED recurring
prices बने हैं (keys `lite_ae` … `pro_ae_yearly` in STRIPE_PRICE_IDS)।

## 0. एक मिनट में पूरी बात (TL;DR)

- FarryOn **voice-first** है। हर live voice मिनट पर हमें **~$0.012** लगता है। यही ~95% खर्च है।
- बाकी सब services (vision, web search, WhatsApp, email) मिलाकर एक active user पर **~$0.20–0.35/माह**।
- **सुनहरा नियम (No-Loss Rule):** हर paid plan का cap इतना रखो कि user पूरा cap खा ले तब भी profit में हो।
  अगर ये सच है → **किसी भी paid user पर कभी loss नहीं।**
- **Free = 60 मिनट one-time trial** (रोज़ नहीं, ज़िंदगी में एक बार)। खर्च per signup ~$0.72, **एक बार**।
- 10,000 users पर अनुमानित **~$1,950/माह मुनाफ़ा**। घाटा सिर्फ़ शुरुआती 100–300 users पर (सामान्य)।

---

## 1. हर paid service की लागत (कुछ नहीं छूटा)

FarryOn के codebase से निकाली गई पूरी सूची:

| # | Service | कहाँ लगता है | Per-unit cost | नोट |
|---|---------|-------------|---------------|-----|
| 1 | **Gemini Live (voice)** | हर live session का हर मिनट | **~$0.012 / मिनट** | 🔴 मुख्य खर्च (~95%) |
| 2 | Google Cloud Vision | `identify_image`, `/detect` | ~$0.003 / scan | पहले 1,000/माह free; landmark+web = 2 unit |
| 3 | Gemini flash (product explain) | vision के बाद का AI जवाब | ~$0.0004 / call | नगण्य |
| 4 | Web search (Serper / Tavily) | `web_search` tool | ~$0.002 / search | Tavily free 1,000/माह, फिर paid |
| 5 | WhatsApp Business | `send_whatsapp` | ~$0.004+ / conversation | **optional** — free `wa.me` link fallback मौजूद |
| 6 | Telegram | `send_telegram` | **free** | Bot + MTProto दोनों free |
| 7 | Email (SMTP) | password reset, email tools | **~free** | flat provider cost |
| 8 | Google/MS SSO, Maps links | login, location | **free** | सिर्फ़ links, कोई API bill नहीं |
| 9 | **Stripe fee** | हर payment पर | **2.9% + $0.30** | 🔴 revenue से सीधे कटता है (India/intl ~3–4%) |
| 10 | **Hosting (Render + Postgres)** | पूरा backend | **flat ~$25–150/माह** | per-user नहीं, fixed |

**निष्कर्ष:** असली खेल **voice minutes** का है। बाकी सब मिलाकर active user पर ~$0.20–0.35/माह — जिसका
बड़ा हिस्सा free tiers में ढँक जाता है।

> कोड में मौजूद cost-safety features (पहले से): per-user daily quota, vision "on-demand only"
> (कैमरा सिर्फ़ पूछने पर model तक जाता है), context compression, session time cap। इनकी वजह से
> infra scale के लिए तैयार है।

---

## 2. सुनहरा नियम: No-Loss (इसे कभी मत तोड़ो)

> **हर paid plan का cap इतना रखो कि user cap पूरा खा ले, तब भी वो profit में हो।**

अगर ये सच है, तो एक paid user पर आप **कभी loss में नहीं आ सकते** — चाहे वो कितना भी use करे।
और हकीकत में ज़्यादातर users cap का सिर्फ़ 40–60% ही use करते हैं, तो असली margin और ऊँचा।

Free tier को no-loss बनाने का तरीका: **daily recurring free मत दो — one-time trial दो।**
इससे free user का खर्च per signup सिर्फ़ एक बार होता है, हर महीने नहीं।

---

## 3. Plan ढाँचा (अभी config में set किया हुआ)

`backend/app/config.py → plan_catalog` — plans की एकमात्र जगह:

| Plan | कीमत/माह | Voice budget | असली रूप | Image scans | Web searches |
|------|----------|--------------|----------|-------------|--------------|
| **Free** | $0 | **60 मिनट one-time (lifetime)** | बोझ नहीं, असली taste | 3/day | 10/day |
| **Lite** ⭐ | **$5** | **150 मिनट/माह (~5 min/day)** | सस्ता entry | 20/day | 50/day |
| **Plus** | $10 | 360 मिनट/माह (~12 min/day) | regular users | 50/day | 100/day |
| **Pro** | $20 | 900 मिनट/माह (~30 min/day) | heavy users | unlimited | 200/day |

**Voice enforcement कैसे होता है:**
- **Monthly plans** — महीने का budget रोज़ के cap में बँटता है (`voice_minutes ÷ 30`)। तो Lite user
  को रोज़ ~5 मिनट मिलते हैं; वो एक ही दिन में पूरा महीना नहीं उड़ा सकता।
- **Trial (Free)** — 60 मिनट पूरी ज़िंदगी का एक-बार budget; रोज़ का नहीं। इसे user के **all-time**
  voice से नापा जाता है, आज के usage से नहीं।

> बदलना हो? सिर्फ़ `plan_catalog` में `voice_minutes` या `price_usd` बदलो — daily caps और billing
> table अपने-आप अपडेट हो जाते हैं।

---

## 4. "$5 में कितने मिनट?" — पूरा हिसाब

$5 revenue से सब घटाओ:

```
Revenue                                $5.00
− Stripe fee (2.9% + $0.30)           −$0.45   → net $4.55
− बाकी services (vision/search/etc.)   −$0.20   → $4.35 बचा
```

बचे $4.35 को voice + profit में बाँटो ($0.012/min पर):

| Voice cap | मिनट/माह | ≈ रोज़ | Voice cost | बचा profit | Gross margin |
|-----------|----------|--------|-----------|-----------|--------------|
| कम (safe) | 130 | ~4.5 min | $1.56 | $2.79 | ~56% |
| **संतुलित (चुना हुआ)** | **150** | **~5 min** | **$1.80** | **$2.55** | **~51%** |
| उदार | 200 | ~6.5 min | $2.40 | $1.95 | ~39% |

**जवाब: $5/माह में आराम से 150 मिनट (~5 min/day) @ ~50% margin।** इससे नीचे margin मत जाने दो।

---

## 5. हर plan का Unit P&L (worst case = user पूरा cap खाए)

ये "सबसे बुरी हालत" का हिसाब है — user रोज़ पूरा cap use करे। असल में इससे कम use होता है, तो असली
profit ज़्यादा।

| Plan | Revenue | − Stripe | − Voice (max) | − बाकी services | **= Profit/user/माह** | Margin |
|------|---------|----------|---------------|-----------------|----------------------|--------|
| Lite $5 | $5.00 | $0.45 | $1.80 (150 min) | $0.20 | **$2.55** | ~51% |
| Plus $10 | $10.00 | $0.59 | $4.32 (360 min) | $0.30 | **$4.79** | ~48% |
| Pro $20 | $20.00 | $0.88 | $10.80 (900 min) | $0.40 | **$7.92** | ~40% |

हर pंक्ति में profit **positive** है → No-Loss Rule पास। ✅

**Free (trial) की worst-case लागत:** 60 min × $0.012 = **$0.72 — एक बार, per signup** (recurring नहीं)।

---

## 6. Scale पर P&L: 100 → 10,000 users

**मान्यताएँ (साफ़-साफ़):**
- 5% users paid बनते हैं (industry-सामान्य freemium conversion)।
- औसत paid profit ~$5/user/माह (Lite-heavy mix; ऊपर table से)।
- Free = 60-min one-time trial; नए signups का one-time burn।
- Hosting step-wise बढ़ता है।
- Trial burn ≈ महीने में जुड़ने वाले नए free users × $0.72 (मोटा अनुमान)।

| Users | Paid (5%) | Paid profit/माह | Free trial burn/माह | Hosting | **Net/माह** |
|-------|-----------|-----------------|---------------------|---------|-------------|
| 100 | 5 | $25 | ~$30 | $25 | **≈ −$30** (झेल लो — marketing) |
| 500 | 25 | $125 | ~$60 | $25 | **≈ +$40** |
| 1,000 | 50 | $250 | ~$90 | $50 | **≈ +$110** |
| 3,000 | 150 | $750 | ~$180 | $100 | **≈ +$470** |
| 5,000 | 250 | $1,250 | ~$250 | $100 | **≈ +$900** |
| 10,000 | 500 | $2,500 | ~$400 | $150 | **≈ +$1,950/माह** |

**पढ़ने का तरीका:** paid users structurally profit में हैं (नियम #2), इसलिए जितना बढ़ोगे उतना मुनाफ़ा।
छोटा घाटा सिर्फ़ पहले 100–300 users पर — नए product के लिए बिल्कुल सामान्य।

> **अगर conversion 5% से कम हो?** तो free trial छोटा करो (जैसे 30 min) या Lite को थोड़ा promote करो।
> हर 1% conversion बढ़ना 10K पर ~$500/माह जोड़ता है।

---

## 7. "User आसानी से आए + बोझ न पड़े" कैसे

1. **Free — बिना credit card:** 60 मिनट का असली trial (रोज़ 1 min की झुंझलाहट नहीं)। User पहले
   value चखता है, फिर पैसे की बात।
2. **किफ़ायती entry — Lite $5:** $10/$20 से पहले एक सस्ता कदम। यही "बोझ न पड़े" का जवाब।
3. **Regional/PPP pricing:** भारत/एशिया में कम कीमत, US/UAE में पूरी — बाद में जोड़ो।
4. **सालाना discount** (~2 महीने free): upfront cash + लोग टिकते हैं।

---

## 8. तीन सुरक्षा-कवच (ताकि loss कभी न हो)

1. **Company-wide monthly FREE-budget cap** *(अभी लागू नहीं — अगला काम)*: एक env limit —
   "इस महीने free voice पर कुल $X से ज़्यादा नहीं"। हिट होते ही नए free trials रुकें। आख़िरी ब्रेक।
2. **हर paid tier का hard cap** *(लागू है)*: `plan_limits` + `quota_enforcement_enabled`।
3. **Paid services में हमेशा free-fallback** *(लागू है)*: WhatsApp → `wa.me` link,
   web search → पहले free tiers (Tavily/Serper)।

---

## 9. लागू करने की स्थिति (कोड में)

| आइटम | स्थिति |
|------|--------|
| Gemini = default AI provider | ✅ हो गया (`config.py → ai_provider`) |
| `plan_catalog` — single source of truth (price + caps) | ✅ हो गया |
| Lite $5 / Plus $10 / Pro $20 caps | ✅ set |
| Free = 60-min one-time trial (lifetime enforcement) | ✅ हो गया (session + repo) |
| Billing table config से seed | ✅ हो गया (`seed.py`) |
| Stripe price IDs इन tiers से wire | ⏳ बचा (keys चाहिए) |
| Company-wide free-budget cap (कवच #1) | ⏳ बचा (नया feature) |
| Device par live test (voice cap, trial लगना) | ⏳ बचा |

---

## 10. मुख्य नंबर एक नज़र में

| चीज़ | मान |
|------|-----|
| Voice cost | **$0.012 / मिनट** |
| बाकी services (per active user) | **~$0.20–0.35 / माह** |
| Stripe fee | **2.9% + $0.30** |
| Free trial लागत | **$0.72 (एक बार / signup)** |
| $5 Lite → voice | **150 min/माह (~5 min/day), ~51% margin** |
| 10K users अनुमानित मुनाफ़ा | **~$1,950 / माह** |

> सब मान्यताएँ रूढ़िवादी (conservative) रखी हैं। असल usage cap से कम होता है, इसलिए असली मुनाफ़ा
> इन नंबरों से ऊपर रहने की संभावना है — नीचे नहीं।

</details>
