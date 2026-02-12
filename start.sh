#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ROOT_DIR}/environment.yml"
PID_FILE="${ROOT_DIR}/.winggen.pid"
UI_DIR="${ROOT_DIR}/ui/terminal"
ENV_NAME="winggen"

if ! command -v conda >/dev/null 2>&1; then
  echo "conda is required but not found in PATH" >&2
  exit 1
fi

if ! conda env list | awk '{print $1}' | grep -qx "${ENV_NAME}"; then
  echo "Creating conda environment '${ENV_NAME}' from ${ENV_FILE}..."
  conda env create -f "${ENV_FILE}"
fi

if [[ ! -f "${UI_DIR}/package.json" ]]; then
  echo "Missing UI package.json at ${UI_DIR}" >&2
  exit 1
fi

if [[ ! -d "${UI_DIR}/node_modules" ]]; then
  echo "Installing UI dependencies in conda env '${ENV_NAME}'..."
  conda run -n "${ENV_NAME}" npm --prefix "${UI_DIR}" install
fi

if [[ -f "${PID_FILE}" ]]; then
  existing_pid="$(cat "${PID_FILE}")"
  if kill -0 "${existing_pid}" >/dev/null 2>&1; then
    echo "WingGen is already running (PID ${existing_pid}). Use ./stop.sh first." >&2
    exit 1
  fi
  rm -f "${PID_FILE}"
fi

echo "Starting WingGen Ink UI..."
conda run -n "${ENV_NAME}" npm --prefix "${UI_DIR}" run start >"${ROOT_DIR}/.winggen.log" 2>&1 &
pid="$!"
echo "${pid}" > "${PID_FILE}"

echo "WingGen started (PID ${pid})."
echo "Logs: ${ROOT_DIR}/.winggen.log"
echo "Use ./stop.sh to stop."
