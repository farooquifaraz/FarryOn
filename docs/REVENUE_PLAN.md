# FarryOn — Revenue, Cost & P&L Plan

> **इस document का मकसद:** FarryOn का पूरा पैसा-हिसाब एक जगह, आसान भाषा में — कहाँ खर्च होता है,
> कहाँ से कमाई आती है, और हर पैमाने (100 → 10,000 users) पर profit/loss (P&L) क्या रहेगा।
> जो भी plan बदलना हो, सिर्फ़ `backend/app/config.py` के `plan_catalog` में बदलो — बाकी सब
> (daily caps, billing table) अपने-आप उसी से बनता है।

---

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
