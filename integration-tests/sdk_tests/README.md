# SDK tests

Tests the TypeScript, Python and Go SDKs against a running app. The workflow is
`.github/workflows/sdk-tests.yml`; every test step in it calls a script in this
folder, so the same run works on a laptop. It runs on pull requests and every night.

| Run | SDK | Verdict when it fails |
| --- | --- | --- |
| Current | Generated from this checkout's spec | BLOCK |
| Previous | The tags in `.github/sdk-test-matrix.yaml` | FLAG |

The previous run starts only for the languages whose current run passed. The report
goes to the job summary, a comment on the pull request, and Slack
(`SLACK_BOT_TOKEN`, channel `vars.SLACK_CI_CHANNEL_ID`). The Slack message has the
results table; its thread has the failed cases and a zip of the reports folder:
JUnit files, each suite's output and exit code, the generation, provisioning and
coverage output, and the Docker logs of every service.

## When the verdict is FLAG

The change breaks customers who use the last released SDK. It does not stop the
merge, but a person must accept it:

1. The pull request gets the label `breaks-previous-sdk` and a failed
   `SDK break approval` status.
2. Read the failures in the report, and add a note for the changelog.
3. Add the label `sdk-break-approved`. Anyone with triage access can. The status
   turns green and names you (`.github/workflows/sdk-break-approval.yml`).

An approval covers the failures it was given for. If a later run fails other cases,
the job removes `sdk-break-approved` and the status fails again. A later OK run removes
both labels. A previous run that writes no results (a failed checkout or install) is
also a FLAG. After an SDK release, change its tag in `.github/sdk-test-matrix.yaml`.

## Where the tests live

- **Generated tests:** `backend/nodejs/apps/src/modules/api-docs/tests.arazzo.yaml`.
  One workflow there becomes one test in each SDK.
- **Hand-written tests:** `handwritten/<language>/`. For what Speakeasy cannot generate
  a test for: anything that answers with a stream, anything that needs an item read out
  of a list or a response header, waiting for a connector sync, and every expected error
  status (a generated test calls the operation and fails on any error). The three
  languages have the same tests, and `handwritten/operations.txt` lists the operations
  they call. `generate_sdks.sh` copies them into the generated SDK.

## Rules for a test

The three suites run at the same time against one app.

- Check only what the test created, by ID. Never check a count, or a position in a
  list another suite can write to.
- Delete what the test created.
- Do not change organization settings or the AI model setup. For an operation that
  changes them, test only requests the server refuses or that change nothing.
- Some names are unique in an org (connectors, OAuth configurations). A hand-written test
  adds a random suffix; a workflow uses `x-env: PIPESHUB_SDK_TEST_NAME`, which
  `run_sdk_tests.sh` sets per suite, once per workflow.
- The OAuth app and personal access token routes accept only an interactive session. A
  workflow for them sets `x-speakeasy-test-security` to `bearerAuth: "x-env:
  PIPESHUB_SESSION_TOKEN"` (`with_oauth_app.py` signs in and sets it). A step's own
  `x-speakeasy-test-security` stays in force for the steps after it.
- In `tests.arazzo.yaml`: a step whose output a later step uses needs a
  response-body condition; conditions support only `==` and `!=`; a condition value
  cannot contain spaces or refer to a step output; do not read a list item by
  position. Speakeasy turns a case it cannot generate into a skipped test, and the
  report counts a skipped test as a failure.
- Do not put a condition on a field the spec types as a one-value enum, such as
  `success` with `enum: [true]`. The Go SDK has no field for it, so the generated
  Go test does not compile.
- Compare only strings and booleans in a response-body condition. The generated
  Go test compares a number as an `int`, so an `int64` field never equals it
  (`$statusCode` conditions are fine).

## Run it locally

Needs a running app, the Speakeasy CLI with `SPEAKEASY_API_KEY`, Node, Python and
Go 1.24, and a checkout of `pipeshub-ai/open-api` at the `OPEN_API_REF` in the
workflow. The Go SDK's `go.mod` can ask for a newer Go; with a newer Go installed,
use `GOTOOLCHAIN=go1.24.9+auto`, as Speakeasy's staticcheck cannot read packages
built by Go 1.27.

Run it against a stack you can throw away, not a dev instance: provisioning creates
an org, an OAuth app, AI models and a knowledge base.

```bash
cd integration-tests/sdk_tests

# 1. Org, AI models and one indexed document.
python provision_sdk_test_env.py

# 2. Generate the SDKs and their tests.
./generate_sdks.sh ~/open-api /tmp/sdk-work

# 3. Current run, then the previous run for the SDKs that passed. with_oauth_app.py
#    gives each run an OAuth app and a session JWT (the PIPESHUB_* variables).
./with_oauth_app.py ./run_sdk_tests.sh current /tmp/sdk-work /tmp/sdk-reports
./checkout_released_sdks.sh ../../.github/sdk-test-matrix.yaml /tmp/sdk-released
./with_oauth_app.py ./run_sdk_tests.sh previous /tmp/sdk-released /tmp/sdk-reports

# 4. Verdict and report.
python report.py /tmp/sdk-reports --out /tmp/sdk-reports/report --releases ../../.github/sdk-test-matrix.yaml
```

`provision_sdk_test_env.py` documents the environment variables it reads.
