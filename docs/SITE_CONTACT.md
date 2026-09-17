# Website contact buttons and social icons

How a visitor on the marketing page reaches a human, and what happened to the
links that used to go nowhere.

---

## What changed

The page shipped with **eleven links pointing at `#`** — four social icons,
Changelog, About, Blog, Careers, Press, SDK Docs and WhatsApp — and a "Sign in"
pointing at `/login`.

On a page asking AED 350 from a brand the visitor has not heard of, a link that
does nothing when clicked is not cosmetic. It is their evidence that nobody is
home. All of them are gone.

| Link | What happened |
| --- | --- |
| 4 social icons (`#`) | Now driven by settings. Set a profile URL → that icon appears. None set → no icons. |
| Footer **WhatsApp** (`#`) | Now a real `wa.me` link, from `WHATSAPP_NUMBER`. |
| "Questions? WhatsApp us" (plain text) | Now an actual button. |
| **Changelog, Blog, Careers, Press, SDK Docs** | Removed. |
| **About** (`#`) | Now a real page at `/about`, linked from the nav and the footer. |
| **Company** footer column | The old one (About/Blog/Careers/Press, all dead) is gone. The Support column is now "Company": About, FAQ, WhatsApp, Privacy, Terms. Footer is 3 columns. |
| **Sign in** → `/login` (nav + footer) | Removed. See below. |

### Why "Sign in" had to go

`/login` is not a 404 — and that is the problem. The edge proxy forwards a
short list of paths to the backend and sends *everything else* to the admin
SPA, which has its own `/login`. So a customer clicking "Sign in" on the public
site landed on the **staff admin panel login**, which checks their password,
finds they are not an admin, and refuses them.

Customers sign in inside the mobile app. There is no customer web login, so the
public site no longer offers one.

---

## Setting the WhatsApp number

One setting turns on every WhatsApp button on the page:

```bash
WHATSAPP_NUMBER="+971 50 123 4567"
WHATSAPP_HOURS="We reply within a few hours · Sun–Thu, 9am–6pm GST"
```

Write the number however you like — `+971 50 123 4567`, `971501234567` and
`(971) 50-123-4567` all work; everything that is not a digit is stripped. It
must include the country code. A number that cannot work (too short, letters,
country code missing) renders **no button at all** rather than one that opens a
chat with nobody.

> `WHATSAPP_TOKEN` and `WHATSAPP_PHONE_ID` are something else entirely —
> Business API credentials the *assistant* sends messages with. This is just
> the number printed on the website.

### Where the buttons appear

| Where | Button |
| --- | --- |
| Every page, bottom-right | Floating green button. Steps aside over the closing banner, which has its own. |
| L801 card | "Ask about the L801 Business" |
| L802 card | "Ask about the L802 Premium" |
| Closing banner | "Questions? Chat on WhatsApp" + your reply hours |
| Footer → Support | "WhatsApp" |

### Each button says where it came from

Every button opens the chat with a different message already typed:

- L802 card → *"Hi FarryOn! I'd like to know more about the L802 Premium glasses."*
- Closing banner → *"Hi FarryOn! I have a question before I order."*
- Footer → *"Hi FarryOn! I need some help."*

So an enquiry arrives already telling you which card sent it. That is free to
capture here and impossible to reconstruct afterwards — after a month you can
see whether the L801 or the L802 is the one people ask about. The wording lives
in `MESSAGES` in `backend/app/web/contact.py`.

### Two things worth getting right

**Use a business number, not a personal one.** It is on a public page; it will
be scraped. WhatsApp Business (the free app) is enough at current volume and
adds a catalogue, quick replies, labels and an away message.

**Set `WHATSAPP_HOURS`.** A button nobody answers for two days is worse than no
button: the visitor reads silence as being ignored. Stating the hours turns the
same delay into an expectation that was met.

---

## Social icons

Each network is independent — set the ones that exist:

```bash
SOCIAL_INSTAGRAM=https://instagram.com/yourhandle
SOCIAL_LINKEDIN=https://linkedin.com/company/yourcompany
SOCIAL_YOUTUBE=https://youtube.com/@yourchannel
SOCIAL_FACEBOOK=https://facebook.com/yourpage
SOCIAL_X=https://x.com/yourhandle
```

A network whose page is not up yet can be set to `soon`:

```
SOCIAL_LINKEDIN=soon
SOCIAL_YOUTUBE=soon
```

That shows the icon greyed with a small "Soon" under it, unlinked — a footer
with one lonely Instagram icon reads as a brand with no presence, while a
hidden network is a promise nobody can see. Replace `soon` with the URL when
the page exists. (Live since 2026-09-17: Instagram real, LinkedIn/YouTube soon.)

Only the ones with a URL render. Anything that is not an `http(s)` URL is
ignored rather than written into an `href`.

The icons are now the brands' own marks drawn as SVG. They used to be the emoji
📷 💼 💬 ▶, which render as a camera, a briefcase, a speech bubble and a
triangle on a good half of the devices that saw them.

---

## The About page

`/about` is the page a visitor opens before trusting an unfamiliar brand with
AED 350, and the one a distributor reads before replying. It is written from
what the product and the rest of the site already say — the two models, what
the assistant does today, the "works with any Bluetooth headset" fact from the
FAQ, the privacy commitments from the privacy page, Dubai, the 30-day return,
the 14-day trial — and deliberately nothing else.

It makes **no claim the site cannot back**: no founding year, no founder
names, no team size, no customer numbers, no "patented", no investors. A test
(`test_the_about_page_makes_no_claim_the_site_cannot_back`) fails if one of
those words is added without the fact behind it. When there is a real story to
tell — who started it and why — it belongs in a short section after "The idea",
and that test's word list should be updated alongside.

The page lives in `backend/app/web/about.html`, is served by `GET /about` in
`router.py`, takes the same contact pass as the landing page (so its closing
WhatsApp button appears with `WHATSAPP_NUMBER`), and is listed in Caddy's
forward list — the link tests below check all three.

---

## Checking it

```bash
cd backend && python -m pytest tests/test_site_links.py tests/test_site_contact.py -q
```

`test_site_links.py` is the part that keeps this from coming back. It asserts,
on every build:

1. **No link points at `#`** — configured or not.
2. **Every internal link has a backend route** — this is what "Sign in" failed.
3. **Every linked path is in the edge proxy's forward list** — a route can be
   written, tested and working while Caddy quietly sends it to the admin SPA
   instead. That is not hypothetical: it is exactly what happened to the
   product galleries' `/media` route.

A generic link checker would catch the first of those and miss the two that
actually bit.
