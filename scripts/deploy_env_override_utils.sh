#!/usr/bin/env bash

# Capture and restore explicitly provided env vars so sourced defaults files
# cannot overwrite invocation-time values.
DEPLOY_ENV_CAPTURE_NAMES=()
DEPLOY_ENV_CAPTURE_SET=()
DEPLOY_ENV_CAPTURE_VALUES=()

deploy_env_capture_overrides() {
  DEPLOY_ENV_CAPTURE_NAMES=("$@")
  DEPLOY_ENV_CAPTURE_SET=()
  DEPLOY_ENV_CAPTURE_VALUES=()

  local name
  for name in "${DEPLOY_ENV_CAPTURE_NAMES[@]}"; do
    if [[ "${!name+x}" == "x" && -n "${!name}" ]]; then
      DEPLOY_ENV_CAPTURE_SET+=("1")
      DEPLOY_ENV_CAPTURE_VALUES+=("${!name}")
    else
      DEPLOY_ENV_CAPTURE_SET+=("0")
      DEPLOY_ENV_CAPTURE_VALUES+=("")
    fi
  done
}

deploy_env_restore_overrides() {
  local idx
  for idx in "${!DEPLOY_ENV_CAPTURE_NAMES[@]}"; do
    if [[ "${DEPLOY_ENV_CAPTURE_SET[idx]}" == "1" ]]; then
      printf -v "${DEPLOY_ENV_CAPTURE_NAMES[idx]}" '%s' "${DEPLOY_ENV_CAPTURE_VALUES[idx]}"
      export "${DEPLOY_ENV_CAPTURE_NAMES[idx]}"
    fi
  done
}
