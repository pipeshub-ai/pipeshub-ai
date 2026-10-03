#!/usr/bin/env bash
# Purge must delete only this cluster's default bucket, and must empty a
# versioned bucket in batches of at most 1000 keys before delete-bucket.
# No AWS account and no network.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
DEPLOY="$ROOT/deployment/helm/aws/deploy.sh"
INSTALL="$ROOT/deployment/helm/aws/install.sh"
PRELOAD="$ROOT/deployment/helm/pipeshub-ai/templates/s3-sigv4-preload-configmap.yaml"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PASS_FILE="$TMP/pass"
FAIL_FILE="$TMP/fail"
: >"$PASS_FILE"
: >"$FAIL_FILE"
pass() { printf '  ok   - %s\n' "$1"; echo x >>"$PASS_FILE"; }
fail() { printf '  FAIL - %s\n' "$1"; printf '%s\n' "$1" >>"$FAIL_FILE"; }

extract_fn() {
  awk -v fn="$1" '$0 ~ "^"fn"\\(\\) \\{"{g=1} g{print} g && $0 == "}"{exit}' "$2"
}

for fn in default_bucket_name resolve_purge_bucket empty_versioned_bucket destroy; do
  extract_fn "$fn" "$DEPLOY" >"$TMP/${fn}.sh"
  if ! grep -q "^${fn}()" "$TMP/${fn}.sh"; then
    fail "extract ${fn}"
  fi
done

# shellcheck disable=SC1091
source "$TMP/default_bucket_name.sh"
# shellcheck disable=SC1091
source "$TMP/resolve_purge_bucket.sh"
# shellcheck disable=SC1091
source "$TMP/empty_versioned_bucket.sh"
# shellcheck disable=SC1091
source "$TMP/destroy.sh"

die() { printf 'DIE %s\n' "$*" >&2; exit 1; }
step() { printf 'STEP %s\n' "$*"; }
note() { printf 'NOTE %s\n' "$*"; }

ACCOUNT_ID=111111111111
REGION=us-east-1
CLUSTER=pipeshub
EXPECTED="pipeshub-${CLUSTER}-${ACCOUNT_ID}-${REGION}"
OTHER="pipeshub-pipeshub-sandbox-${ACCOUNT_ID}-${REGION}"

# --- bucket guard -----------------------------------------------------------
(
  BUCKET="$OTHER"
  resolve_purge_bucket
) >"$TMP/refuse.out" 2>"$TMP/refuse.err" && fail "other cluster bucket was accepted" || {
  refuse_err="$(cat "$TMP/refuse.err")"
  if [[ "$refuse_err" == *"refusing to delete s3://${OTHER}"* && "$refuse_err" == *"s3://${EXPECTED}"* ]]; then
    pass "purge refuses another cluster's bucket and names this one"
  else
    fail "purge refusal message"
    printf '         %s\n' "$refuse_err"
  fi
}

(
  BUCKET=""
  resolve_purge_bucket
  printf '%s\n' "$BUCKET"
) >"$TMP/default.out" 2>"$TMP/default.err"
if [[ "$(cat "$TMP/default.out")" == "$EXPECTED" ]]; then
  pass "unset bucket resolves to the default name"
else
  fail "default bucket name"
  printf '         got: %s\n' "$(cat "$TMP/default.out")"
fi

(
  confirm() { printf 'CONFIRM %s\n' "$1"; exit 42; }
  BUCKET=""
  # shellcheck disable=SC2034
  PURGE=true
  # shellcheck disable=SC2034
  DOMAIN=""
  destroy
) >"$TMP/confirm.out" 2>"$TMP/confirm.err"
confirm_out="$(cat "$TMP/confirm.out")"
bucket_line="$(grep -n "s3://${EXPECTED}" "$TMP/confirm.out" | head -1 | cut -d: -f1)"
confirm_line="$(grep -n '^CONFIRM ' "$TMP/confirm.out" | head -1 | cut -d: -f1)"
if [[ -n "$bucket_line" && -n "$confirm_line" && "$bucket_line" -lt "$confirm_line" ]]; then
  pass "exact bucket name is printed before confirmation"
else
  fail "bucket name before confirmation"
  printf '         %s\n' "$confirm_out"
fi
if grep -q 'delete-bucket' "$TMP/confirm.out" "$TMP/confirm.err"; then
  fail "confirmation path deleted a bucket"
else
  pass "confirmation path does not delete the bucket"
fi

# --- empty a versioned bucket ----------------------------------------------
AWS_LOG="$TMP/aws.log"
DELETE_COUNTS="$TMP/counts"
: >"$AWS_LOG"
: >"$DELETE_COUNTS"
mkdir -p "$TMP/bin"
cat >"$TMP/bin/aws" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"${AWS_LOG:?}"
op=""
prev=""
delete_file=""
has_marker=false
for arg in "$@"; do
  case "$arg" in
    list-object-versions|delete-objects|list-multipart-uploads|abort-multipart-upload|delete-bucket)
      op="$arg" ;;
    --key-marker) has_marker=true ;;
  esac
  if [[ "$prev" == --delete ]]; then
    delete_file="${arg#file://}"
  fi
  prev="$arg"
done
case "$op" in
  list-object-versions)
    if $has_marker; then
      python3 - <<'PY'
import json
print(json.dumps({
    "Versions": [{"Key": "last", "VersionId": "v-last"}],
    "IsTruncated": False,
}))
PY
    else
      python3 - <<'PY'
import json
versions = [{"Key": "k%s" % i, "VersionId": "v%s" % i} for i in range(999)]
markers = [{"Key": "marker", "VersionId": "m1"}]
print(json.dumps({
    "Versions": versions,
    "DeleteMarkers": markers,
    "IsTruncated": True,
    "NextKeyMarker": "page-2",
    "NextVersionIdMarker": "ver-2",
}))
PY
    fi
    ;;
  delete-objects)
    python3 - "$delete_file" <<'PY'
import json, os, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
objs = payload["Objects"]
if len(objs) > 1000:
    sys.stderr.write("batch %s\n" % len(objs))
    raise SystemExit(1)
missing = [o for o in objs if not o.get("VersionId")]
if missing:
    sys.stderr.write("missing VersionId\n")
    raise SystemExit(1)
with open(os.environ["DELETE_COUNTS"], "a", encoding="utf-8") as handle:
    handle.write("%s\n" % len(objs))
if os.environ.get("FAIL_DELETE") == "1" and len(objs) == 1000:
    print(json.dumps({"Errors": [{"Key": objs[0]["Key"], "VersionId": objs[0]["VersionId"], "Code": "AccessDenied", "Message": "denied"}]}))
else:
    print("{}")
PY
    ;;
  list-multipart-uploads)
    if [[ -f "$TMP_UPLOAD_LISTED" ]]; then
      echo '{"IsTruncated": false}'
    else
      touch "$TMP_UPLOAD_LISTED"
      echo '{"Uploads":[{"Key":"partial-object","UploadId":"upload-1"}],"IsTruncated": false}'
    fi
    ;;
  abort-multipart-upload|delete-bucket)
    ;;
  *)
    printf 'unexpected aws call: %s\n' "$*" >&2
    exit 1
    ;;
esac
EOF
chmod +x "$TMP/bin/aws"

export AWS_LOG DELETE_COUNTS
export TMP_UPLOAD_LISTED="$TMP/upload-listed"
export PATH="$TMP/bin:$PATH"
export FAIL_DELETE=0

(
  set -euo pipefail
  empty_versioned_bucket "pipeshub-pipeshub-111111111111-us-east-1"
  aws s3api delete-bucket --bucket "pipeshub-pipeshub-111111111111-us-east-1"
) >"$TMP/empty.out" 2>"$TMP/empty.err"
empty_status=$?
if [[ "$empty_status" -ne 0 ]]; then
  fail "empty versioned bucket (exit ${empty_status})"
  printf '         %s\n' "$(cat "$TMP/empty.err")"
else
  counts="$(awk 'NR>1{printf ","} {printf "%s", $0}' "$DELETE_COUNTS")"
  if [[ "$counts" == "1000,1" ]]; then
    pass "1001 versions are deleted in batches of at most 1000"
  else
    fail "delete batch sizes"
    printf '         got: %s\n' "$counts"
  fi
fi
abort_line="$(grep -n 'abort-multipart-upload' "$AWS_LOG" | head -1 | cut -d: -f1 || true)"
bucket_line="$(grep -n 'delete-bucket' "$AWS_LOG" | head -1 | cut -d: -f1 || true)"
if [[ -n "$abort_line" && -n "$bucket_line" && "$abort_line" -lt "$bucket_line" ]] \
  && grep -q 'partial-object' "$AWS_LOG" && grep -q 'upload-1' "$AWS_LOG"; then
  pass "incomplete multipart upload is aborted before delete-bucket"
else
  fail "multipart abort order"
  printf '         abort=%s delete-bucket=%s\n' "$abort_line" "$bucket_line"
fi

: >"$AWS_LOG"
: >"$DELETE_COUNTS"
rm -f "$TMP_UPLOAD_LISTED"
export FAIL_DELETE=1
if (
  set -euo pipefail
  empty_versioned_bucket "pipeshub-pipeshub-111111111111-us-east-1"
) >"$TMP/err-delete.out" 2>"$TMP/err-delete.err"; then
  fail "delete-objects Errors was treated as success"
else
  err_text="$(cat "$TMP/err-delete.err")"
  if [[ "$err_text" == *AccessDenied* && "$err_text" == *"failed to delete objects"* ]]; then
    pass "delete-objects Errors fails the purge"
  else
    fail "delete-objects Errors message"
    printf '         %s\n' "$err_text"
  fi
  if grep -q 'abort-multipart-upload' "$AWS_LOG" || grep -q 'delete-bucket' "$AWS_LOG"; then
    fail "purge continued after a delete error"
  else
    pass "a delete error stops before abort and delete-bucket"
  fi
fi

if grep -q 'progress_stop="$(mktemp -u)"' "$INSTALL"; then
  pass "helm progress sentinel is not created before the loop"
else
  fail "install.sh progress sentinel"
fi
if grep -q 'signatureVersion' "$PRELOAD" && ! grep -q 'catch' "$PRELOAD"; then
  pass "sigv4 preload failure stops the process"
else
  fail "sigv4 preload still swallows require errors"
fi

passed="$(wc -l <"$PASS_FILE" | tr -d ' ')"
failed="$(wc -l <"$FAIL_FILE" | tr -d ' ')"
printf '\n%s passed, %s failed\n' "$passed" "$failed"
[[ "$failed" -eq 0 ]]
