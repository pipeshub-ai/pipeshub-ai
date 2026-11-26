#!/usr/bin/env bash
# The PostgreSQL alert rules (deployment/monitoring/prometheus/postgres.rules.yml) with the promtool of the Prometheus image the
# monitoring overlay runs: the rules parse, the unit tests fire each key alert on a synthetic series and keep it quiet on a healthy one,
# and prometheus.yml loads the overlay's scrape jobs and rules.
set -euo pipefail
cd "$(dirname "$0")/../../monitoring/prometheus"
image="$(sed -nE 's/.*\$\{PROMETHEUS_IMAGE:-([^}]+)\}.*/\1/p' ../../docker-compose/docker-compose.monitoring.yml)"
[ -n "$image" ] || { echo "no Prometheus image in docker-compose.monitoring.yml" >&2; exit 1; }
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
touch "$work/worker-credential"   # promtool check config stats the harness job's credential file
chmod 755 "$work"; chmod 644 "$work/worker-credential"
promtool() { docker run --rm --network none --memory 256m --entrypoint promtool "$@"; }
promtool -v "$PWD:/w:ro" -w /w "$image" check rules postgres.rules.yml
promtool -v "$PWD:/w:ro" -w /w/tests "$image" test rules postgres.rules.test.yml
promtool -v "$PWD/prometheus.yml:/etc/prometheus/prometheus.yml:ro" -v "$work:/run/pxh-monitoring:ro" \
  -v "$PWD/agent-harness.rules.yml:/etc/prometheus/rules/agent-harness.rules.yml:ro" \
  -v "$PWD/postgres.rules.yml:/etc/prometheus/rules/postgres.rules.yml:ro" \
  -v "$PWD/postgres.scrape.yml:/etc/prometheus/scrape.d/postgres.yml:ro" \
  "$image" check config /etc/prometheus/prometheus.yml
# the overlay mounts exactly these files where prometheus.yml reads them
for f in postgres.scrape.yml:/etc/prometheus/scrape.d/postgres.yml postgres.rules.yml:/etc/prometheus/rules/postgres.rules.yml; do
  grep -qF -- "- ../monitoring/prometheus/$f:ro" ../../docker-compose/docker-compose.monitoring-postgres.yml \
    || { echo "docker-compose.monitoring-postgres.yml does not mount $f" >&2; exit 1; }
done
echo "postgres alert rules: ok"
