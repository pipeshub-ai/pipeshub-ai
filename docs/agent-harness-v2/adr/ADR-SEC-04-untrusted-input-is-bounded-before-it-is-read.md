# ADR-SEC-04: untrusted input is bounded before it is read

- **Findings:** SEC-C-01, 05, 06, 07, 08, 09, 15, 16, plus SEC-C-17 and SEC-C-18 from this package's sweep
- **Scope:** Node's public and internal listeners, the shared token vector, every archive and upload reader, and
  the egress proxy's response path

## Context

The R37 area C audit found the same fault eight times. Bytes from someone not yet verified, or a few compressed
bytes, were read in full before any check. Several readers also cost more than linear time:

- a quadratic regex ran before authentication;
- a tar was enumerated whole before its limits;
- `testzip()` inflated every member;
- a chunked upload was trusted on its `Content-Length`;
- 10 MB of JSON was parsed before the worker credential was checked;
- the rate limiter skipped by path substring;
- the egress proxy forwarded a reason phrase unscrubbed.

Each reader bounded itself its own way, or not at all. The sweep found the same shapes again: in the global XSS
check that runs before authentication (SEC-C-17), and in the regexes the worker runs over engine and model
output (SEC-C-18).

## Decision

Every reader of untrusted bytes follows one order:

1. **Authenticate.**
2. **Bound.**
3. **Read** with a reader whose cost is linear in what it is allowed to read.

Each layer is shared, so a new reader inherits it.

1. **Authenticate first.**
   - The pasted-secret guard runs inside the authenticator of every router that takes a person's text
     (`scanAfterAuthentication`). A route without it fails a structural test.
   - The internal listener parses no body at the app level. Each guard brings its route's JSON parser after it
     (`thenJson`, `runTokenRoute`), sized to that route class: 1 MB for the worker credential, 10 MB for an
     attempt token, 256 KB for a run token.
   - An edition route's body is parsed only when a bearer is present.
   - The global limiter skips an `internal` path only when a scoped token verifies there. A path is the
     caller's to choose, so it is never trusted alone.
2. **Bound before reading.**
   - A person's text field is refused past `CHAT_QUERY_MAX_CHARS` before it is scanned. The response is 400, as
     the schema answers, so a steer and a new turn are refused alike.
   - Routes that take a person's text parse JSON at 2 MB, not the shared 10 MB.
   - Every multipart reader is built by `createMulter`, which counts bytes as they arrive, whether declared or
     chunked. It refuses past a cap, which is either explicit or derived from its file limits.
   - The workspace upload streams to a private temporary folder and reads one file at a time. One person may
     stream two uploads at once.
   - Python reads archives only through `harness_core/archive_reader.py`. It enforces:
     - per-header limits on entry size, total size and entry count;
     - the end record's entry count before the central directory is parsed;
     - an `ExpansionMeter` on every decompressor, with a ratio check;
     - refusal of path traversal, links and special files;
     - streaming to disk when asked.
   - The XSS sanitizer takes at most 2 MiB of text that contains angle brackets per request.
3. **Linear readers, proven by structure.**
   - Every regex that scans attacker-controlled text must pass `tests/support/regex_linearity.py`. The rule set
     refuses nested repeats, adjacent overlapping repeats, and a run that is rescanned from each position
     unless a lookbehind, a tempered body or an anchor guards it.
   - The shared token vector is linted once, in Python. Node and the frontend run the same source strings, and a
     Node test holds the generated copy to the contract file.
   - The lint is held to every shape that was measured as quadratic, so a weaker lint fails first.
   - No test reads the host clock for this (WALLCLOCK-1). A real-time growth probe was used during the sweep to
     find the shapes; the lint is what stays.
   - The scanners also take a deadline (`ScanBudget`, `deadline=`) as a net for a regression.
4. **Responses are scrubbed as a whole.** The egress proxy sends every forwarded field through one
   `ResponseScrubber`:
   - the status line gets the standard reason phrase for the status code;
   - header names and values are scrubbed;
   - the body is scrubbed, including across chunk boundaries.
   
   It swaps the real token back to the placeholder in its raw, URL-encoded, hex and base64 forms, and in the whole
   `user:password` of a Basic credential.

### Consequences

- An anonymous request costs a header check. A verified one costs at most its bound, read in linear time.
- A legitimate chunked upload works, and is metered rather than refused.
- A label in an engine's answer cannot contain `[`, and a citation URL cannot contain one either. A bracketed
  IPv6 URL in an engine citation is no longer linked. This was judged rare and acceptable.
- Compose keeps the internal listener on `0.0.0.0` inside the app container. The egress proxy reaches it on
  `sandbox_plane` and Prometheus on `pipeshub`. No Compose file publishes 3002, and a cohesion test holds every
  file to that.
- Residual: `live_citations._CLOSED_REF` is still quadratic within its 2 KiB look-back window per frame
  (bounded). The Excel parser parses a zip's central directory before checking its entry count, inside the
  parse worker pool (SEC-C-19).

## As built

- **Node.**
  - Guard and authentication:
    - `transport/pasted-secret-guard.ts` (`removePastedSecrets`, `scanAfterAuthentication`, `SCANS_AFTER_AUTH`);
    - `domain/pasted-secrets.ts` (`ScanBudget`, `PastedSecretScanTimeout`).
  - Body parsing and readers:
    - `libs/middlewares/person-text-body.middleware.ts`;
    - `libs/utils/multer.utils.ts` (`meterRequestBytes`, `requestDiskStorage`, `readUploadedFile`);
    - `transport/internal-auth.ts` (`thenJson`, `runTokenRoute`, `editionJsonBodies`, `INTERNAL_JSON_LIMITS`).
  - Limits:
    - `libs/middlewares/rate-limit.middleware.ts` (`isVerifiedServiceCall`);
    - `libs/middlewares/xss-sanitization.middleware.ts` (`XSS_MARKUP_BUDGET_CHARS`).
- **Python.**
  - `harness_core/archive_reader.py`.
  - `harness_core/security/pasted_secrets.py` (`redact_prefix`, `deadline=`).
  - `harness_core/sandbox/egress/policy.py` (`ResponseScrubber`, `reflection_variants`).
- **Tests.** `backend/python/tests/support/regex_linearity.py`, plus the tests named in the STATUS rows SEC-C-01..19.
- **Contract.** The token vector (`contracts/harness/vectors/token-patterns.json`) and its generated copies.

## Deviations

- SEC-C-08's proof asked for 411 on a chunked upload. The meter makes chunked uploads safe, so they are accepted
  and counted, which is better for a person whose client streams. The finding allowed this.
- SEC-C-09 proposed binding Compose's listener to the `pipeshub` network only. The egress proxy reaches it on
  `sandbox_plane`, so the bind stays `0.0.0.0` inside the container. Authentication before parsing, plus no
  publication, are the defence.

## Owner amendments

None.

## Status

**Accepted** 2026-11-13: decided from first principles, long term; built by R38-INPUT.
