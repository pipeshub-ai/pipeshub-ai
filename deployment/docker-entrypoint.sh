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

# Volumes written by earlier root-running releases. Finding mismatched owners
# means stat-ing every inode, and local storage keeps a file per record, so the
# full pass runs until it has once finished on a volume; a marker records that.
# Only root can create one the check trusts, so the runtime user cannot skip a
# pass. While it is there only the top two levels are checked, which catches a
# volume restored or copied as root. docker-compose.yml and process_monitor.sh
# delete the markers when services run as root (a rollback to an older image);
# deleting one by hand forces a full pass.
OWNERSHIP_MARKER=.pipeshub-ownership-v1

log() {
    echo "docker-entrypoint: $*" >&2
}

# A single link: a hard link to some root-owned leftover is not a marker.
trusted_marker() {
    [ -f "$1" ] && [ ! -L "$1" ] && [ "$(stat -c %u:%g:%h "$1" 2>/dev/null)" = 0:0:1 ]
}

take_ownership() {
    dir=$1
    marker="$dir/$OWNERSHIP_MARKER"
    if trusted_marker "$marker" && [ -z "$(find "$dir" -xdev -maxdepth 2 ! -path "$marker" \
            \( ! -uid "$APP_UID" -o ! -gid "$APP_GID" \) -print -quit 2>/dev/null)" ]; then
        return 0
    fi
    # Removed before the pass so that an interrupted pass resumes next start.
    rm -f "$marker" || true
    started=$(date +%s)
    if changed=$(find "$dir" -xdev \( ! -uid "$APP_UID" -o ! -gid "$APP_GID" \) \
            -exec chown -h "$APP_UID:$APP_GID" {} + -printf . 2>/dev/null); then
        # mktemp creates exclusively and rename replaces a planted symlink
        # instead of following it, so root writes nothing outside the volume.
        tmp=""
        if ! { tmp=$(mktemp "$marker.XXXXXX" 2>/dev/null) && mv -fT "$tmp" "$marker"; }; then
            [ -z "$tmp" ] || rm -f "$tmp" || true
            log "$dir: could not write $OWNERSHIP_MARKER; the next start checks every file again"
        fi
        log "$dir: gave ${#changed} entries to $APP_UID:$APP_GID in $(($(date +%s) - started))s"
    else
        # Its stderr can be a line per file (a read-only volume), so retry
        # just the first entry still unowned to report where and why.
        first=$(find "$dir" -xdev \( ! -uid "$APP_UID" -o ! -gid "$APP_GID" \) \
            -print -quit 2>/dev/null) || true
        if [ -n "$first" ] && ! err=$(chown -h "$APP_UID:$APP_GID" "$first" 2>&1); then
            err=" ($err)"
        else
            err=""
        fi
        log "$dir: ownership pass did not finish${first:+ at $first}$err; the next start retries it"
    fi
}

for dir in /data/pipeshub /root/.local /root/.cache/huggingface; do
    if [ -d "$dir" ]; then
        take_ownership "$dir"
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
