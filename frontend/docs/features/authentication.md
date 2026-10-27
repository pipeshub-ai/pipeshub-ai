# Authentication Settings — API Reference

## Overview

The Authentication settings page (`/workspace/authentication`) lets admins choose which sign-in methods are active for their organisation, and configure the Google and Microsoft providers.

> **SAML SSO and generic OAuth/OIDC sign-in were removed in PipesHub 1.0.** The upgrade turns them off and falls back to password sign-in. Until everyone affected has a password, the page shows a notice listing the people who have signed in but have no password yet (see [Sign-in removal notice](#sign-in-removal-notice)).

### Route

`app/(main)/workspace/authentication/`

### Key files

| File | Purpose |
|------|---------|
| `page.tsx` | Main page — state, orchestration, UI |
| `api.ts` | All API calls (typed) |
| `types.ts` | TypeScript types & per-method metadata |
| `components/auth-method-row.tsx` | Row for a single auth method |
| `components/configure-panel.tsx` | Right-side panel (reuses `WorkspaceRightPanel`) |
| `components/forms/provider-config-form.tsx` | Google and Microsoft config form, driven by `PROVIDER_CONFIGS` in `constants.ts` |
| `components/sign-in-removal-notice.tsx` | Notice after the 1.0 upgrade turned SAML or OAuth sign-in off |

---

## Supported Auth Methods

| Method type | Label | Configurable | Requires SMTP |
|-------------|-------|:---:|:---:|
| `password` | Password | — | — |
| `otp` | One-Time Password | — | ✓ |
| `google` | Google | ✓ | — |
| `microsoft` | Microsoft | ✓ | — |

**Policy:** Only **one** method can be active at a time.

---

## API Endpoints

All requests go through `apiClient` (axios with auth interceptors from `lib/api/`).

### 1. Get Enabled Auth Methods

```
GET /api/v1/orgAuthConfig/authMethods
```

**Authentication:** Required (admin)

**Response:**
```json
{
  "authMethods": [
    {
      "order": 1,
      "allowedMethods": [
        { "type": "google" }
      ]
    }
  ]
}
```

**Usage:** Called on page mount to populate enable/disable state of each row.

---

### 2. Update Enabled Auth Methods

```
POST /api/v1/orgAuthConfig/updateAuthMethod
```

**Authentication:** Required (admin)

**Request body:**
```json
{
  "authMethod": [
    {
      "order": 1,
      "allowedMethods": [
        { "type": "google" }
      ]
    }
  ]
}
```

**Notes:**
- `allowedMethods` should contain exactly **one** entry at most.
- If `allowedMethods` is empty `[]`, all methods are disabled.
- Called when the user clicks "Save" after toggling methods.

---

### 3. Get Google Auth Config

```
GET /api/v1/configurationManager/authConfig/google
```

**Response:**
```json
{
  "clientId": "your-google-client-id",
  "enableJit": true
}
```

---

### 4. Save Google Auth Config

```
POST /api/v1/configurationManager/authConfig/google
```

**Request body:**
```json
{
  "clientId": "your-google-client-id",
  "enableJit": true
}
```

**Notes:** JIT (Just-In-Time) provisioning creates accounts on first login.

---

### 5. Get Microsoft Auth Config

```
GET /api/v1/configurationManager/authConfig/microsoft
```

**Response:**
```json
{
  "clientId": "your-microsoft-client-id",
  "tenantId": "your-tenant-id",
  "enableJit": true
}
```

---

### 6. Save Microsoft Auth Config

```
POST /api/v1/configurationManager/authConfig/microsoft
```

**Request body:**
```json
{
  "clientId": "your-microsoft-client-id",
  "tenantId": "your-directory-tenant-id",
  "enableJit": true
}
```

---

### 7. Check SMTP Configuration

```
GET /api/v1/configurationManager/smtpConfig
```

**Purpose:** Check whether SMTP is configured so the `otp` method can be enabled.

**Response:**
```json
{
  "host": "smtp.example.com",
  "port": 587,
  "fromEmail": "no-reply@example.com"
}
```

The OTP method is considered "available" when both `host` and `fromEmail` are present.

---

### 8. Get Frontend URL

```
GET /api/v1/configurationManager/frontendPublicUrl
```

**Purpose:** Used to pre-fill the Redirect URI and Authorized Origin fields in config forms.

**Response:**
```json
{
  "url": "https://app.pipeshub.com"
}
```

The redirect URI is built as `{frontendUrl}/auth/{method}/callback`.

The desktop app uses the same redirect URIs: sign-in opens in the user's browser and the callback page hands the result back over a `pipeshub://` link.

### 9. Get Sign-In Removal Status

```
GET /api/v1/orgAuthConfig/signInRemoval
```

**Authentication:** Required (admin)

**Response:**
```json
{
  "removedMethods": ["samlSso"],
  "peopleWithoutPassword": [
    { "userId": "665f…", "fullName": "Alice Chen", "email": "alice@example.com" }
  ]
}
```

`removedMethods` lists what the 1.0 upgrade turned off for this workspace (`samlSso`, `oauth`); it is empty when the workspace used neither. `peopleWithoutPassword` lists people who have signed in but have no password.

---

## UI Behaviour

### Edit Mode
- Click **Edit** to enter edit mode (toggles become interactive)
- Toggles with unmet requirements show a tooltip explaining why they're disabled:
  - OTP requires SMTP
  - Configurable methods (Google, Microsoft) must be configured before enabling
  - Single-method policy enforced: can't enable a method when another is already ON
- SettingsSaveBar appears when there are unsaved changes

### Configure Panel
- Click the **gear icon** on a configurable method to open the config panel
- Panel uses `WorkspaceRightPanel` (slide-in dialog from the right)
- Forms load existing config on mount
- "Save" in the panel ONLY saves the provider configuration — it does **not** enable the method
- On success: updates "Configured" badge + shows success toast

### Sign-in removal notice
- Shown above the method list when the 1.0 upgrade removed SAML or OAuth sign-in for this workspace **and** someone who has signed in still has no password
- Names the removed methods and the methods now on, then lists up to 10 people (name and email) and "and N more"
- Explains how they get a password: "Forgot Password" on the sign-in page (needs SMTP), or a set-password link created by whoever runs the PipesHub server (1.0 upgrade guide)
- Renders nothing once the list is empty, or if the status request fails

### Success Toast
```
Title: "{Provider} Auth successfully configured!"
Description: "Your users can sign in with their {Provider} accounts"
```

---

## Config Is Considered "Configured" When

| Method | Required fields |
|--------|----------------|
| Google | `clientId` |
| Microsoft | `clientId` + `tenantId` |
