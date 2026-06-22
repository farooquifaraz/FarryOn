# 🏛️ Landmark Finder

Kisi bhi landmark image se detail nikalo — naam, location (coordinates),
Google Maps link aur Wikipedia se poori description.

Do tarike hain (dono kaam karte hain):

| Mode | Free? | Kya deta hai |
|------|-------|--------------|
| **Web** | ✅ 100% free, no key | Google Lens link — browser me poori detail |
| **API** | 1000/month free, fir paid | Automated text output: naam + coords + Maps + Wikipedia detail |

## Install
```bash
pip install -r requirements.txt
```

## 1) Web Mode (free, no key)
Web par maujood kisi bhi image URL ke liye:
```bash
python landmark_finder.py "https://example.com/photo.jpg" --web
```
Ye ek Google Lens link banata hai. Link kholte hi Google Lens image ko
pehchan kar landmark + similar images + detail dikha deta hai. Bilkul free.

> Local file ke liye web mode nahi chalega (uska public URL nahi hota).
> Local file hai to API mode use karein.

## 2) API Mode (Cloud Vision — automated detail)
```bash
# API key ke saath (file ya URL dono chalti hai)
python landmark_finder.py "photo.jpg" --api-key YOUR_KEY
python landmark_finder.py "https://example.com/photo.jpg" --api-key YOUR_KEY

# Ya key ko env var me rakhein:
export GOOGLE_VISION_API_KEY=YOUR_KEY      # Windows: set GOOGLE_VISION_API_KEY=YOUR_KEY
python landmark_finder.py "photo.jpg"
```

Output me milega:
- Landmark ka naam + confidence %
- Exact coordinates (lat/long)
- Google Maps link
- Wikipedia se poori description + link

### Cloud Vision API key kaise milegi (free tier)
1. [console.cloud.google.com](https://console.cloud.google.com) par jaayein
2. Naya project banayein
3. **Cloud Vision API** enable karein
4. **APIs & Services → Credentials → Create Credentials → API key**
5. Billing enable karna padega (card chahiye), par **1000 requests/month free**

> Pricing: pehli 1000 requests/month free, uske baad ~$1.50 per 1000.
> Nayi accounts ko $300 free credit (90 din) bhi milta hai.

## Sirf free chahiye, bina card?
Web mode use karein, ya seedhe [lens.google.com](https://lens.google.com) par
photo upload karein — 100% free.
