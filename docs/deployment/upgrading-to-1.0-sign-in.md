# Upgrading to 1.0: SAML and generic OAuth sign-in are removed

PipesHub 1.0 removes two sign-in methods: **SAML single sign-on** and **generic OAuth/OIDC** (the "OAuth" provider with your own authorization, token and user-info URLs). Password, email OTP, Google and Microsoft sign-in stay. **Azure AD** sign-in is now part of **Microsoft** sign-in: it was the same sign-in under another name.

If your workspace never turned on SAML or OAuth, nothing changes for you.

## Before you upgrade

If your workspace uses SAML or OAuth:

1. In **Workspace → Authentication**, turn on another method: password, email OTP, Google or Microsoft.
2. Set up email (SMTP) under **Workspace → Mail**, if it isn't set up yet. People who have only ever signed in through SAML or OAuth have no password; email OTP and "Forgot password" both send email.
3. Tell your users which method to use after the upgrade.

## What the upgrade does by itself

When the server starts on 1.0, it rewrites each workspace's sign-in policy once:

- SAML and OAuth are taken out of every sign-in step. A step left with no method is dropped.
- If the policy named SAML or OAuth and does not offer password sign-in anywhere, password sign-in is added to the first step, so nobody is left without a way in.
- Azure AD becomes Microsoft. If Microsoft sign-in was not set up, the Azure AD client ID and tenant are copied to it.

Your saved SAML and OAuth settings are not deleted; they are no longer used.

Until that rewrite has run, the sign-in page already ignores SAML and OAuth and offers password sign-in instead, so there is no moment when nobody can sign in.

## After you upgrade

Admins see a notice on **Workspace → Authentication** that names the methods the upgrade turned off and lists the people who have signed in but never set a password. Anyone on that list who doesn't sign in with Google, Microsoft or email OTP needs a password:

- **With email set up:** they use **Forgot password** on the sign-in page.
- **Without email:** whoever runs the server creates a single-use link that lets one person choose their password, and sends it to them:

  ```bash
  # Docker Compose
  docker compose exec pipeshub-ai sh -c 'cd /app/backend && node dist/scripts/set-password-link.js person@example.com'

  # Kubernetes (Helm): use your release's deployment name
  kubectl exec deploy/<release>-pipeshub-ai -- sh -c 'cd /app/backend && node dist/scripts/set-password-link.js person@example.com'
  ```

  The link works once and expires after the same time as a "Forgot password" email (`PASSWORD_RESET_LINK_EXPIRY`). This is also how an admin who only ever used SAML gets back in.

The notice goes away once everyone on the list has a password.

## API changes

These endpoints are gone and answer `404`:

- `/api/v1/saml/*` (sign-in, callback, desktop code exchange)
- `POST /api/v1/userAccount/oauth/exchange`
- `GET`/`POST /api/v1/configurationManager/authConfig/sso`, `.../authConfig/oauth`, `.../authConfig/azureAd`, and their `/internal/` variants

`POST /api/v1/userAccount/authenticate` refuses `samlSso`, `oauth` and `azureAd`; use `microsoft` for Azure AD accounts. Saving a sign-in policy (`POST /api/v1/orgAuthConfig/updateAuthMethod`) that names one of them answers `400`.

The `SAML_SP_ENTITY_ID` environment variable no longer has any effect; you can leave it in your deployment files.
