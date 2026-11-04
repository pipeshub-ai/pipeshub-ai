#!/usr/bin/env bash
# Makes sure PostgreSQL <major> has a running "main" cluster that accepts local connections without a password, and prints its
# port. For CI runners, where the pgdg package does not always create the cluster: postgresql-common creates "main" only when no
# other cluster is configured and this is the newest major installed, and the runner image already has PostgreSQL 16 configured.
#   scripts/ensure_pg_cluster.sh 17
set -euo pipefail

ver="${1:?usage: ensure_pg_cluster.sh <major version>}"
fail() {
  echo "ensure_pg_cluster: $*" >&2
  pg_lsclusters >&2 || true
  exit 1
}
port_of() { pg_lsclusters -h | awk -v v="$ver" '$1 == v && $2 == "main" {print $3}'; }

if [ -z "$(port_of)" ]; then
  echo "ensure_pg_cluster: no $ver/main cluster after installing the package; creating it" >&2
  sudo pg_createcluster "$ver" main >&2 || fail "pg_createcluster $ver main failed"
  sudo systemctl daemon-reload || true  # pick up the unit for the new cluster
fi
hba="/etc/postgresql/$ver/main/pg_hba.conf"
[ -f "$hba" ] || fail "$hba does not exist"
sudo sed -i 's/ \(peer\|scram-sha-256\|md5\)$/ trust/' "$hba"
sudo pg_ctlcluster "$ver" main restart >&2 || fail "$ver/main did not start"
port="$(port_of)"
[ -n "$port" ] || fail "$ver/main is not listed after starting"
for _ in $(seq 1 30); do
  if pg_isready -q -h 127.0.0.1 -p "$port"; then
    pg_lsclusters >&2
    echo "$port"
    exit 0
  fi
  sleep 1
done
fail "$ver/main on port $port does not accept connections"
