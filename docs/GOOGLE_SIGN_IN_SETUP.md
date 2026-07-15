# Google sign-in — setup

The code is done on both sides; Google sign-in stays **switched off** until the
credentials below exist. Nothing breaks meanwhile: the app hides its Google
button when `GOOGLE_SERVER_CLIENT_ID` is unset, and the backend answers
`503 SSO_NOT_CONFIGURED` if anything calls the endpoint anyway.

## How the pieces fit

```
 Phone                         Backend                        Google
 ─────                         ───────                        ──────
 google_sign_in
  (native sheet)  ──────────────────────────────────────────► "who are you?"
       │  ◄──────────────── ID token (aud = WEB client id) ──────┘
       │
       └─ POST /api/v1/auth/sso/google/mobile {id_token}
                              │
                              ├─ verify_oauth2_token(token, aud=GOOGLE_CLIENT_ID)
                              │      └──────► fetch Google's signing certs ──►
                              ├─ link_or_create_user(...)   # verified email only
                              └─ returns FarryOn access + refresh tokens
```

The app never decides who signed in — it only carries a token Google issued and
the backend verifies. A forged token gets a 401; an unreachable Google gets a
503 (never a 401, which would blame the user for our outage).

## 1. Google Cloud console

Create the project's OAuth credentials at
<https://console.cloud.google.com/apis/credentials>:

1. **OAuth consent screen** — External, fill the app name/support email, add the
   `email` and `profile` scopes.
2. **Web application** client → this one's client id is what BOTH sides use:
   - backend: `GOOGLE_CLIENT_ID`
   - app: `--dart-define=GOOGLE_SERVER_CLIENT_ID=...`
3. **Android** client → needs the package name and the signing certificate's
   SHA-1. It gets no id in our config; its only job is to authorise the app.
   Create one per signing key you ship with — debug AND release, or Google
   sign-in works on your machine and fails for real users.

```bash
# debug SHA-1
keytool -list -v -alias androiddebugkey -keystore ~/.android/debug.keystore \
        -storepass android -keypass android
# release SHA-1 — use the keystore in mobile/android/key.properties
keytool -list -v -alias <your-alias> -keystore <your.keystore>
```

> **The one that catches everybody:** the app passes the **Web** client id as
> `serverClientId`. Pass the Android one and Google returns an account but a
> null `idToken`, so sign-in fails with no useful error. The app reports
> "Google sign-in isn't configured correctly for this app" for exactly this.

## 2. Backend

```bash
# backend/.env
GOOGLE_CLIENT_ID=<web-client-id>.apps.googleusercontent.com
```

`GOOGLE_CLIENT_SECRET` is only needed for the **web admin panel's** redirect
flow (`/auth/sso/google/login`), not for the phone.

## 3. App

```bash
flutter run \
  --dart-define=GOOGLE_SERVER_CLIENT_ID=<web-client-id>.apps.googleusercontent.com
```

Same flag for `flutter build apk`. Without it the Google button simply isn't
shown — a button that can only fail is worse than no button.

## 4. Before shipping — replace the drawn "G"

`GoogleGlyph` (mobile/lib/features/auth/widgets/auth_bits.dart) draws Google's
mark with a `CustomPainter` so no asset ships and it stays crisp at any size.
It is a close **approximation**, and Google's branding guidelines require their
*unmodified* asset. Download the official mark from
<https://developers.google.com/identity/branding-guidelines> and swap the
painter for an `Image.asset`/`SvgPicture` before release. The button's wording,
neutral surface and layout already follow their rules.

## Windows gotcha: a broken `CURL_CA_BUNDLE` breaks Google verification

The PostgreSQL Windows installer sets a machine-wide
`CURL_CA_BUNDLE=C:\Program Files\PostgreSQL\<v>\ssl\certs\ca-bundle.crt`, and
that file often doesn't exist. `requests` — which google-auth uses to fetch
Google's signing certs — honours that variable and dies on it, so every Google
sign-in fails with `503 SSO_UNAVAILABLE` even though the config is correct.
(The same variable also breaks `pip install`.)

It is a machine problem, not a code one — Render never sets it. Confirm and
clear it:

```bash
echo $CURL_CA_BUNDLE                 # points at a file that isn't there?
ls "$CURL_CA_BUNDLE"                 # "No such file or directory" = this bug
unset CURL_CA_BUNDLE                 # this shell only
```

To fix it for good, remove it from the Windows environment variables (System
Properties → Environment Variables), or repoint it at a real bundle:
`python -c "import certifi; print(certifi.where())"`.

## Verifying it works

The account-linking rule (`modules/sso/service.py`) is covered by
`backend/tests/test_sso.py` without any Google credentials — the ID-token
verification is stubbed, so a forged token, an unreachable Google, an
unverified email and the happy path are all tested offline.

On a device, a successful Google sign-in should:
- land you on the home screen with no password ever typed,
- appear in the admin panel's Users list, and
- write `auth.sso_login` (with `{"provider": "google", "flow": "mobile"}`) to
  the audit log.
