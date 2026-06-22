# 📡 Landmark/Product Finder — JSON API

Single endpoint, har response ek consistent structure me. Kisi bhi app
(mobile/web/backend) se use kar sakte ho.

## Endpoint
```
POST /api/detect
Content-Type: application/json
```

## Common response envelope
Har response ka yahi shape hota hai:
```json
{
  "ok": true,
  "mode": "web | landmark | product",
  "result": { ... }
}
```
Error par:
```json
{
  "ok": false,
  "error": "error message (string)"
}
```
> Hamesha pehle `ok` check karo. `true` hai to `result` padho, warna `error`.

---

## 1. Web mode (free, no key)
**Request**
```json
{ "mode": "web", "image_url": "https://example.com/photo.jpg" }
```
**Response `result`**
```json
{ "lens_url": "https://lens.google.com/uploadbyurl?url=..." }
```

---

## 2. Landmark mode
**Request** (image_url ya image_data me se ek)
```json
{
  "mode": "api",
  "api_key": "CLOUD_VISION_API_KEY",
  "image_url": "https://...",
  "image_data": "data:image/jpeg;base64,...."
}
```
**Response `result`**
```json
{
  "count": 1,
  "landmarks": [
    {
      "name": "National Museum of Qatar",
      "confidence": 0.8731,
      "location": { "lat": 25.28675, "lng": 51.55197 },
      "maps_url": "https://www.google.com/maps?q=25.28675,51.55197",
      "description": "The National Museum of Qatar is ...",
      "wikipedia_url": "https://en.wikipedia.org/wiki/National_Museum_of_Qatar"
    }
  ]
}
```
> `location`, `maps_url`, `description`, `wikipedia_url` `null` ho sakte hain.

---

## 3. Product mode
**Request**
```json
{
  "mode": "product",
  "api_key": "CLOUD_VISION_API_KEY",
  "gemini_key": "GEMINI_API_KEY (optional)",
  "image_url": "https://...",
  "image_data": "data:image/jpeg;base64,...."
}
```
**Response `result`**
```json
{
  "product_name": "Apple Watch Series 9",
  "categories": ["Smartwatch", "Apple Watch", "Wearable"],
  "ai_explanation": "• Ye kya hai: ...\n• Features: ...",
  "marketplaces": [
    { "name": "Amazon UAE", "region": "Gulf",   "search_url": "https://www.amazon.ae/s?k=Apple+Watch+Series+9" },
    { "name": "Noon UAE",   "region": "Gulf",   "search_url": "https://www.noon.com/uae-en/search/?q=..." },
    { "name": "Flipkart",   "region": "India",  "search_url": "https://www.flipkart.com/search?q=..." },
    { "name": "eBay",       "region": "Global", "search_url": "https://www.ebay.com/sch/i.html?_nkw=..." }
  ],
  "matching_pages": [ { "title": "Page title", "url": "https://..." } ],
  "similar_images": [ "https://image1.jpg", "https://image2.jpg" ]
}
```
> `product_name`, `ai_explanation` `null` ho sakte hain (pehchan na ho / Gemini key na ho).

---

## Field types (quick reference)
| Field | Type | Notes |
|-------|------|-------|
| `ok` | bool | hamesha rahega |
| `mode` | string | "web" / "landmark" / "product" |
| `confidence` | float | 0.0 – 1.0 |
| `location` | object/null | `{lat, lng}` |
| `marketplaces[].search_url` | string | seedha browser me khulta hai |
| `ai_explanation` | string/null | Gemini se |

## Example: kisi bhi app se call (JavaScript)
```js
const res = await fetch("http://YOUR_SERVER/api/detect", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ mode: "product", api_key: VISION_KEY, image_url: url })
});
const data = await res.json();
if (data.ok) console.log(data.result.product_name, data.result.marketplaces);
else console.error(data.error);
```
