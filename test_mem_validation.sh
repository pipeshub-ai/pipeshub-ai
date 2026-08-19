#!/usr/bin/env bash
_parse_mem_mb() {
  [[ "$1" =~ ^([0-9]+)[gG]$ ]] && { echo $(( ${BASH_REMATCH[1]} * 1024 )); return; }
  [[ "$1" =~ ^([0-9]+)[mM]$ ]] && { echo "${BASH_REMATCH[1]}"; return; }
  return 1
}
test_case() {
  APP_MEMORY_LIMIT="$1"
  APP_MEMSWAP_LIMIT="$2"
  _app_mem_mb="$(_parse_mem_mb "${APP_MEMORY_LIMIT:-12G}")" || _app_mem_mb=""
  _app_memswap_mb="$(_parse_mem_mb "${APP_MEMSWAP_LIMIT:-16G}")" || _app_memswap_mb=""
  if [[ -n "$_app_mem_mb" && -n "$_app_memswap_mb" ]] && (( _app_memswap_mb < _app_mem_mb )); then
    echo "CASE ($1,$2): WOULD DIE - mem_mb=$_app_mem_mb swap_mb=$_app_memswap_mb"
  else
    echo "CASE ($1,$2): OK - mem_mb=$_app_mem_mb swap_mb=$_app_memswap_mb"
  fi
}
test_case "12G" "16G"
test_case "20G" "16G"
test_case "" ""
test_case "8G" "8G"
test_case "20G" ""
test_case "500M" "16G"
test_case "-1" "-1"
test_case "20g" "24g"
