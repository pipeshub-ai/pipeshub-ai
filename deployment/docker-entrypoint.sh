#!/bin/sh
# Starts the app as the unprivileged runtime user. When the container starts as
# root (compose, plain `docker run`), it first prepares what releases that ran
# as root left behind, then drops to that user with no capabilities. When it is
# already started as that user (the Helm chart sets runAsUser), it just execs.
set -eu

APP_UID=1000
APP_GID=1000

if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

# Volumes written by earlier root-running releases. Only mismatched entries are
# touched, so after the first start this is a quick scan.
for dir in /data/pipeshub /root/.local /root/.cache/huggingface; do
    if [ -d "$dir" ]; then
        find "$dir" -xdev \( ! -uid "$APP_UID" -o ! -gid "$APP_GID" \) \
            -exec chown -h "$APP_UID:$APP_GID" {} + 2>/dev/null || true
    fi
done

# Compose files from earlier releases mount the Docker socket into this
# container for the coding sandbox. Keep that working for them by granting the
# socket's group; current compose files reach Docker through docker-socket-proxy
# and mount no socket here.
groups="$APP_GID"
if [ -S /var/run/docker.sock ]; then
    groups="$groups,$(stat -c %g /var/run/docker.sock)"
fi

exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --groups="$groups" \
    --inh-caps=-all --bounding-set=-all --no-new-privs -- "$@"
