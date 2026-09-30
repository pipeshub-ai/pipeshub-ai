# GHSA-cf48-qh3f-g3g9: signed download URLs not bound to the redeeming user

**Severity:** Medium (reported High) · **Component:** Python connectors service, `app/connectors/api/router.py`, `app/core/signed_url.py`, `app/api/routes/chatbot.py`, `app/utils/logger.py` · **Status:** fixed, pending review

## Vulnerability

The cross-org part of the report was already closed by #3118 (v0.8.0). Issuing a signed URL checks the caller's org and user against the path and runs the record ACL, and redeeming one checks the caller's org, the path org, the record's org, and the org inside the token.

What stayed open was same-org:

- `download_file` (`GET /api/v1/index/{org_id}/{connector}/record/{record_id}?token=`) never compared the token's `user_id` with the caller, and never re-ran the record ACL. Its own comment said so. A colleague who got hold of a URL (logs, browser history, a pasted link) could download the file under their own session until it expired, about 60 minutes. The content was also fetched from the connector as the minting user, not the caller.
- A token without `org_id` in its claims (the "legacy" branch) skipped the token-org check. Tokens live 60 minutes, so no real token needs this branch any more.
- The `purpose: file_processing` claim was written but never checked. Signed-URL tokens are signed with `scopedJwtSecret`, the same secret as service tokens, so only the field layout kept one from being used as the other.
- `SignedUrlHandler.get_signed_url` logged the user id at INFO, and `validate_token` logged the decoded payload at DEBUG.
- uvicorn's access log wrote the full request path, so every `?token=` was stored in the logs verbatim. Node already redacts these; Python did not.

A sibling issue with the same shape was fixed in the same change:

- `DELETE /chat/attachments/{record_id}` (`chatbot.py`) only checked that the record was in the caller's org. It did not check that the record was a chat attachment or that the caller owned it, so any member could delete any record in the org, including knowledge-base and connector records, bypassing knowledge-base delete permissions.

The other sibling from the triage notes, `stream_record` switching to the record's org on a mismatch, was already fixed on `main` by #3675.

## Fix

`download_file`:

- Validates the token with `required_claims={"purpose": SIGNED_URL_PURPOSE}`.
- Reuses `_caller_org_and_user` (the same helper as the issuing route): an unauthenticated caller gets 401, and the caller's org must equal the path org.
- A session caller must be the user the token was minted for (`caller userId == payload.user_id`). Any other user gets 404 even if the record ACL would allow them; they can mint their own URL.
- The token's `org_id` claim is now required and must equal the record's org. The legacy no-org branch is removed.
- For session callers, `check_record_access_with_details(user_id, org, record_id)` runs again at redeem time, so losing access revokes an outstanding URL.
- Indexing service tokens (`connector:signedUrl` scope, no user) keep the org and record checks only, same as the issuing route.

`signed_url.py`: adds `SIGNED_URL_PURPOSE`; `validate_token` requires `exp`; the user-id and payload log lines are removed; payload validation errors log field names only.

`chatbot.py` `delete_chat_attachment`: the record must have `connectorName == ATTACHMENTS` and the caller must hold an OWNER permission edge on it (the existing `_records_owned_by` helper). Otherwise 404.

`logger.py` / `url_redaction.py`: a new `AccessLogRedactionFilter` on `uvicorn.access` replaces the values of sensitive query params with `[REDACTED]`. It uses `redact_sensitive_query_params`, whose parameter list mirrors `SENSITIVE_QUERY_PARAMS` in the Node `log-redaction.utils.ts`.

Token format is unchanged: tokens minted before this change carry `org_id` and `purpose` already (since #3118), so outstanding URLs keep working for their minting user.

Files touched:

- `backend/python/app/connectors/api/router.py`
- `backend/python/app/core/signed_url.py`
- `backend/python/app/api/routes/chatbot.py`
- `backend/python/app/utils/logger.py`
- `backend/python/app/utils/url_redaction.py`
- Tests: `tests/unit/connectors/api/test_router_part1.py`, `test_router_deep1.py`, `test_router_update_config.py`, `tests/unit/core/test_signed_url.py`, `tests/unit/api/routes/test_chatbot.py`, `test_chatbot_extended_95_coverage.py`, `tests/unit/utils/test_logger.py`, `tests/unit/utils/test_url_redaction.py`

## Automated tests

Run from `backend/python`:

```
.venv/bin/python -m pytest -p no:cacheprovider -W ignore -o addopts="" \
  tests/unit/connectors/api/ tests/unit/core/test_signed_url.py \
  tests/unit/utils/test_logger.py tests/unit/utils/test_url_redaction.py \
  tests/unit/api/routes/test_chatbot_extended_95_coverage.py tests/unit/api/routes/test_chatbot.py
```

Result: 1777 passed. With the five app files reverted to `main` and the tests kept, the new cases fail (the exploit succeeds on the old code).

New cases:

- `TestDownloadFile::test_same_org_user_other_than_minter_is_404`: a colleague's session cannot redeem the URL.
- `TestDownloadFile::test_minter_who_lost_record_access_is_404`: the ACL re-check revokes an outstanding URL.
- `TestDownloadFile::test_token_without_org_id_claim_is_404`: legacy branch removed.
- `TestDownloadFile::test_purpose_claim_is_required`, `test_unauthenticated_caller_is_401`.
- `TestDownloadFile::test_indexing_service_token_skips_user_binding_and_acl`: the service path still works.
- `TestSignedUrlHardening`: token without `exp` rejected; user id not logged.
- `TestDeleteChatAttachmentAuthz`: non-attachment record and another user's attachment are not deleted.
- `TestAccessLogRedactionFilter`, `TestRedactSensitiveQueryParams`: `?token=` does not reach the access log.

Replaced: `test_legacy_token_without_org_id_claim_still_downloads`, which asserted the removed behaviour.

## Manual testing before production

Prerequisites on staging: one org with users `A` and `B`, a record `$REC` that A can read and B cannot, and session JWTs `$JWT_A` and `$JWT_B`.

1. Mint a URL. `curl -H "Authorization: Bearer $JWT_A" https://staging/api/v1/$ORG/$A_ID/$CONNECTOR/record/$REC/signedUrl` returns `{"signedUrl": ...}` (call the connectors service directly, port 8088).
2. Minter can redeem. `curl -H "Authorization: Bearer $JWT_A" "$SIGNED_URL"` returns the file. Regression: 404.
3. Exploit blocked. `curl -H "Authorization: Bearer $JWT_B" "$SIGNED_URL"` returns 404 `Record not found`. Regression: 200 with the file.
4. ACL re-check. Remove A's access to `$REC`, then repeat step 2. Expect 404. Regression: 200.
5. No token in logs. `grep 'token=' ` on the connectors service log for the step 2 request shows `token=[REDACTED]`.
6. Chat attachment delete (query service, port 8000). As B, `curl -X DELETE -H "Authorization: Bearer $JWT_B" https://staging/api/v1/chat/attachments/$KB_RECORD` (a KB record, or A's attachment) returns 404 and the record still exists. As A, deleting A's own attachment returns 204.
7. Indexing still works. Upload a file to a KB and confirm it reaches `COMPLETED`. Indexing reads via `/api/v1/internal/stream/record/{id}`, not this route, but this confirms nothing else depended on the old behaviour.

## Rollout notes

- No config, env, migration or secret rotation required. Token format unchanged.
- Behaviour change: a signed URL now works only for the user it was minted for, and only while that user can still read the record. Nothing in the repo redeems these URLs under a different user (indexing uses the internal stream route with a service token, and the frontend never calls the issuing route), but external integrations that pass a URL to another user will now get 404.
- Behaviour change: service accounts cannot delete their own chat attachments through `DELETE /chat/attachments/{record_id}`. Their uploads get an org READER edge, not an OWNER edge. The frontend's call is fire-and-forget, so this only leaves the record in place.
- Not in scope: signed-URL tokens still share `scopedJwtSecret` with service tokens. The `purpose` check closes the practical confusion; a separate key is a follow-up. Also out of scope from the triage notes: unauthenticated internal services (extraction, parsing, docling, embedding), caller-chosen expiry on the Node storage download route, and the indexing service not checking the host of a broker-supplied `signedUrl`.
