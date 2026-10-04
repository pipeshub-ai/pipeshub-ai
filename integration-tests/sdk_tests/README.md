# SDK tests

Tests the TypeScript, Python and Go SDKs against a running app. Every step of the
SDK tests workflow calls a script in this folder, so the same run works on a laptop.

| Run | SDK | Verdict when it fails |
| --- | --- | --- |
| Current | Generated from this checkout's spec | BLOCK |
| Previous | The tags in `.github/sdk-test-matrix.yaml` | FLAG |

## Where the tests live

- **Generated tests:** `backend/nodejs/apps/src/modules/api-docs/tests.arazzo.yaml`.
  One workflow there becomes one test in each SDK.
- **Hand-written tests:** `handwritten/<language>/`. For operations Speakeasy cannot
  generate a test for: anything that answers with a stream, and anything that needs
  an item read out of a list. `generate_sdks.sh` copies them into the generated SDK.

## Rules for a test

The three suites run at the same time against one app.

- Check only what the test created, by ID. Never check a count, or a position in a
  list another suite can write to.
- Delete what the test created.
- Do not change organization settings or the AI model setup.
- In `tests.arazzo.yaml`: a step whose output a later step uses needs a
  response-body condition; conditions support only `==` and `!=`; a condition value
  cannot contain spaces or refer to a step output; do not read a list item by
  position. Speakeasy turns a case it cannot generate into a skipped test, and the
  report counts a skipped test as a failure.

## Run it locally

Needs a running app, the Speakeasy CLI with `SPEAKEASY_API_KEY`, Node, Python and
Go 1.24, and a checkout of `pipeshub-ai/open-api`.

```bash
cd integration-tests/sdk_tests

# 1. Org, OAuth app, AI models and one indexed document. Writes the PIPESHUB_* variables.
SDK_TEST_ENV_PATH=/tmp/sdk.env python provision_sdk_test_env.py
set -a; source /tmp/sdk.env; set +a

# 2. Generate the SDKs and their tests.
./generate_sdks.sh ~/open-api /tmp/sdk-work

# 3. Current run, then the previous run for the SDKs that passed.
./run_sdk_tests.sh current /tmp/sdk-work /tmp/sdk-reports
./checkout_released_sdks.sh ../../.github/sdk-test-matrix.yaml /tmp/sdk-released
./run_sdk_tests.sh previous /tmp/sdk-released /tmp/sdk-reports

# 4. Verdict and report.
python report.py /tmp/sdk-reports --out /tmp/sdk-reports/report
```

`provision_sdk_test_env.py` documents the environment variables it reads.
