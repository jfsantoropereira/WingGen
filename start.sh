#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ROOT_DIR}/environment.yml"
PID_FILE="${ROOT_DIR}/.winggen.pid"
STUDIO_PID_FILE="${ROOT_DIR}/.winggen.studio.pid"
UI_DIR="${ROOT_DIR}/ui/terminal"
ENV_NAME="winggen"
DAEMON_MODE=0
STUDIO_MODE=0

for arg in "$@"; do
  case "${arg}" in
    --daemon) DAEMON_MODE=1 ;;
    --studio) STUDIO_MODE=1 ;;
    *)
      echo "Unknown option: ${arg} (supported: --studio, --daemon)" >&2
      exit 1
      ;;
  esac
done

if ! command -v conda >/dev/null 2>&1; then
  echo "conda is required but not found in PATH" >&2
  exit 1
fi

if ! conda env list | awk '{print $1}' | grep -qx "${ENV_NAME}"; then
  echo "Creating conda environment '${ENV_NAME}' from ${ENV_FILE}..."
  conda env create -f "${ENV_FILE}"
fi

if [[ ${STUDIO_MODE} -eq 1 ]]; then
  STUDIO_CMD=(conda run --no-capture-output -n "${ENV_NAME}" \
    python -m wingopt.studio --config "${ROOT_DIR}/configs/default_wing.toml")
  cd "${ROOT_DIR}"
  export PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"

  if [[ ${DAEMON_MODE} -eq 1 ]]; then
    if [[ -f "${STUDIO_PID_FILE}" ]]; then
      existing_pid="$(cat "${STUDIO_PID_FILE}")"
      if kill -0 "${existing_pid}" >/dev/null 2>&1; then
        echo "WingGen Studio is already running (PID ${existing_pid}). Use ./stop.sh first." >&2
        exit 1
      fi
      rm -f "${STUDIO_PID_FILE}"
    fi

    echo "Starting WingGen Studio server in daemon mode..."
    "${STUDIO_CMD[@]}" >"${ROOT_DIR}/.winggen.studio.log" 2>&1 &
    pid="$!"
    echo "${pid}" > "${STUDIO_PID_FILE}"

    echo "WingGen Studio started in background (PID ${pid})."
    echo "Logs: ${ROOT_DIR}/.winggen.studio.log"
    echo "Use ./stop.sh to stop."
    exit 0
  fi

  echo "Starting WingGen Studio server in interactive mode..."
  exec "${STUDIO_CMD[@]}"
fi

if [[ ! -f "${UI_DIR}/package.json" ]]; then
  echo "Missing UI package.json at ${UI_DIR}" >&2
  exit 1
fi

if [[ ! -d "${UI_DIR}/node_modules" ]]; then
  echo "Installing UI dependencies in conda env '${ENV_NAME}'..."
  conda run --no-capture-output -n "${ENV_NAME}" npm --prefix "${UI_DIR}" install
fi

if [[ ${DAEMON_MODE} -eq 1 ]]; then
  if [[ -f "${PID_FILE}" ]]; then
    existing_pid="$(cat "${PID_FILE}")"
    if kill -0 "${existing_pid}" >/dev/null 2>&1; then
      echo "WingGen is already running (PID ${existing_pid}). Use ./stop.sh first." >&2
      exit 1
    fi
    rm -f "${PID_FILE}"
  fi

  echo "Starting WingGen Ink UI in daemon mode..."
  conda run --no-capture-output -n "${ENV_NAME}" npm --prefix "${UI_DIR}" run start >"${ROOT_DIR}/.winggen.log" 2>&1 &
  pid="$!"
  echo "${pid}" > "${PID_FILE}"

  echo "WingGen started in background (PID ${pid})."
  echo "Logs: ${ROOT_DIR}/.winggen.log"
  echo "Use ./stop.sh to stop."
  exit 0
fi

echo "Starting WingGen Ink UI in interactive mode..."
echo "(Use q to quit, r or Enter to run, arrow keys to adjust inputs.)"
exec conda run --no-capture-output -n "${ENV_NAME}" npm --prefix "${UI_DIR}" run start
