#!/bin/sh
# Sourced by docker-entrypoint.sh and process_monitor.sh. With PIPESHUB_OFFLINE
# on, it sets the switches that keep every library off the network, except the
# ones the operator has already set. Keep the list in step with
# backend/python/app/offline.py (a test compares the two).

pipeshub_offline_on() {
    case "$(printf %s "${PIPESHUB_OFFLINE:-}" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

pipeshub_apply_offline_env() {
    pipeshub_offline_on || return 0
    _set=""
    _kept=""
    for _pair in TELEMETRY_COLLECTION_FLAG=false HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
            HF_DATASETS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 LITELLM_LOCAL_MODEL_COST_MAP=True \
            DO_NOT_TRACK=1; do
        _name=${_pair%%=*}
        _value=${_pair#*=}
        eval "_current=\${$_name:-}"
        if [ -n "$_current" ]; then
            _kept="$_kept $_name"
        else
            export "$_name=$_value"
            _set="$_set $_name"
        fi
    done
    # Quiet when a parent (the entrypoint) already applied it.
    if [ -n "$_set" ] || [ -z "${PIPESHUB_OFFLINE_LOGGED:-}" ]; then
        echo "offline mode (PIPESHUB_OFFLINE): set${_set:- nothing}; operator values kept:${_kept:- none}" >&2
    fi
    export PIPESHUB_OFFLINE_LOGGED=1
}

pipeshub_apply_offline_env
