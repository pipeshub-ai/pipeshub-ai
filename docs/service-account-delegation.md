# Acting on behalf of someone

> **Enterprise edition.** Delegation — letting a service account act as a
> named person — is part of the Enterprise edition. The Community build ships
> a stub router in its place: the capability probe answers `available: false`
> and the rest of the delegation routes answer `501 Not Implemented`, so the
> screen can explain the capability rather than fail at it.
>
> A service account working with **its own** access is in both editions:
> creating one, granting it narrow rights, and issuing or revoking its tokens.
> This page describes the Enterprise half.

A service account normally reads with its own permissions. Delegation lets it
read as a named person instead, for a few minutes at a time, so an integration
can answer "what can *this* person see" rather than returning the union of what
everyone can see.

This is off until someone turns it on, and on a Community build nobody can.
A service account with an empty allow-list can act as nobody, and the grant
route that would fill the allow-list is the 501 stub described above, so there
is no writer and the capability stays dormant. On an Enterprise build the same
sentence has its usual meaning: the feature is present and grants nothing until
an administrator names a person.

## What an administrator does

In **Workspace → Service accounts**, the *Who it can act as* button on a
service account opens a panel with two halves:

- **The allow-list.** Add one person at a time, by email address, with an
  optional reason that whoever reviews the list later will read. An entry whose
  person has since left, been disabled or become an administrator stays
  visible and is marked, rather than quietly disappearing — the screen should
  not disagree with what the system does.
- **The log.** Every change to the list, and every attempt this account made
  to act as someone — including the ones that were refused. A run of refusals
  is what a stolen credential looks like, so refusals are recorded as
  deliberately as successes. Grants and revocations are recorded too, so
  withdrawing a permission does not erase who gave it, to whom, and why.

Administrators cannot be named. Service accounts are held to member rights so
that no machine credential carries admin, and a delegated admin token would
walk around that rule by construction.

## What an integration does

Exchange the service token for a short-lived token for one person, following
[RFC 8693](https://www.rfc-editor.org/rfc/rfc8693):

```bash
curl -X POST https://your-instance/api/v1/oauth2/token \
  --data-urlencode 'grant_type=urn:ietf:params:oauth:grant-type:token-exchange' \
  --data-urlencode "subject_token=$PIPESHUB_SERVICE_TOKEN" \
  --data-urlencode 'requested_subject=person@example.com'
```

```json
{
  "access_token": "phsvc_…",
  "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
  "token_type": "Bearer",
  "expires_in": 300,
  "scope": "kb:read semantic:write"
}
```

There is no client id or secret. The service token is the authentication: it
already carries a verified identity and the scopes that bound it, so asking for
a second credential would only mean handing one out more widely.

`requested_subject` takes an email address or a user id. `scope` is optional
and defaults to whatever the service token holds; naming scopes narrows the
delegated token but can never widen it past the service token.

The token that comes back is an ordinary user token. Send it as a bearer token
anywhere a personal token would go, including `/mcp`.

## What it refuses, and why

| Situation | Response |
|---|---|
| A personal access token offered for exchange | `invalid_grant` |
| A delegated token offered for exchange again | `invalid_grant` |
| The service account is disabled or deleted | `invalid_grant` |
| The person is not on the allow-list | `invalid_target` |
| No such person, an administrator, a disabled person, or another service account | `invalid_target` |
| Scopes beyond the service token's own | `invalid_scope` |

Every refusal about *who* was asked for returns the same error and the same
wording. A credential that could tell "no such person" from "not on your list"
would be a working directory of the organisation and a map of its
administrators. The real cause is in the delegation log, which only an
administrator can read.

## The limits that make it safe

| | |
|---|---|
| Lifetime | 5 minutes, and no refresh token |
| Scopes | At most what the service token holds; `agent:execute` never |
| Allow-list | Named people only, at most 200 per account |
| Audit | Every grant, revocation and attempt kept for 400 days |
| Chaining | A delegated token cannot be exchanged again |

Every rule above is checked again on each request a delegated token makes, not
only when it was issued. Withdrawing a permission, promoting the person to
administrator, or switching the service account off all take effect at once
rather than after the token's last few minutes.

That means every path, including the OpenID endpoints. `/oauth2/userinfo` is
authenticated by a different middleware from the rest of the API, so the rules
live in one shared place and both call it. Otherwise a delegated token carrying
`profile` or `email` would go on returning the person's name and address there
for the rest of its five minutes after the permission behind it was withdrawn.

Disabling a service account also revokes the tokens it obtained, rather than
leaving them to be refused one by one. Deleting one empties its allow-list, so
restoring the account under the same name does not bring back a list of people
somebody else can now become.

## What it does not do

Delegation is read-only, like service tokens themselves: `agent:execute` is the
write surface and is never delegated. There is no per-person consent — an
administrator decides, and the person being acted as is not asked. If that
matters for your organisation, the log is where you would see it happening.
