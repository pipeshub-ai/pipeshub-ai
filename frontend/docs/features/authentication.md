# Authentication Settings — API Reference

## Overview

The Authentication settings page (`/workspace/authentication`) lets admins configure which authentication method is active for their organisation, and configure OAuth/SAML providers.

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
| `components/forms/google-config-form.tsx` | Google OAuth config form |
| `components/forms/microsoft-config-form.tsx` | Microsoft OAuth config form |
| `components/forms/saml-config-form.tsx` | SAML SSO config form |
| `components/forms/oauth-config-form.tsx` | Generic OAuth 2.0 config form |

---

## Supported Auth Methods

| Method type | Label | Configurable | Requires SMTP |
|-------------|-------|:---:|:---:|
| `password` | Password | — | — |
| `otp` | One-Time Password | — | ✓ |
| `google` | Google | ✓ | — |
| `microsoft` | Microsoft | ✓ | — |
| `samlSso` | SAML SSO | ✓ | — |
| `oauth` | OAuth | ✓ | — |

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

### 7. Get SAML SSO Config

```
GET /api/v1/configurationManager/authConfig/sso
```

**Response:**
```json
{
  "entryPoint": "https://idp.example.com/sso/saml",
  "certificate": "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----",
  "emailKey": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
  "logoutUrl": "https://idp.example.com/logout",
  "entityId": "https://your-app.example.com",
  "enableJit": true
}
```

---

### 8. Save SAML SSO Config

```
POST /api/v1/configurationManager/authConfig/sso
```

**Request body:** Same shape as the GET response. `logoutUrl` and `entityId` are optional.

**Required fields:** `entryPoint`, `certificate`, `emailKey`

---

### 9. Get OAuth Config

```
GET /api/v1/configurationManager/authConfig/oauth
```

**Response:**
```json
{
  "clientId": "your-oauth-client-id",
  "providerName": "Okta",
  "authorizationUrl": "https://your.okta.com/oauth2/v1/authorize",
  "tokenEndpoint": "https://your.okta.com/oauth2/v1/token",
  "userInfoEndpoint": "https://your.okta.com/oauth2/v1/userinfo",
  "scope": "openid email profile",
  "redirectUri": "https://app.example.com/auth/oauth/callback",
  "enableJit": true
}
```

Note: `clientSecret` is **not** returned for security reasons (it's stored encrypted).

---

### 10. Save OAuth Config

```
POST /api/v1/configurationManager/authConfig/oauth
```

**Request body:** Same shape as the GET response, but **include** `clientSecret`.

**Required fields:** `clientId`, `clientSecret`, `providerName`, `authorizationUrl`, `tokenEndpoint`, `userInfoEndpoint`

---

### 11. Check SMTP Configuration

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

### 12. Get Frontend URL

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

---

## UI Behaviour

### Edit Mode
- Click **Edit** to enter edit mode (toggles become interactive)
- Toggles with unmet requirements show a tooltip explaining why they're disabled:
  - OTP requires SMTP
  - Configurable methods (Google, Microsoft, SAML, OAuth) must be configured before enabling
  - Single-method policy enforced: can't enable a method when another is already ON
- SettingsSaveBar appears when there are unsaved changes

### Configure Panel
- Click the **gear icon** on a configurable method to open the config panel
- Panel uses `WorkspaceRightPanel` (slide-in dialog from the right)
- Forms load existing config on mount
- "Save" in the panel ONLY saves the provider configuration — it does **not** enable the method
- On success: updates "Configured" badge + shows success toast

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
| SAML SSO | `entryPoint` + `certificate` + `emailKey` |
| OAuth | `clientId` + `providerName` |
