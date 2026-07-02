#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

stop_pid_file() {
  local pid_file="$1"
  local name="$2"

  if [[ ! -f "${pid_file}" ]]; then
    echo "No running ${name} instance found (missing ${pid_file})."
    return 0
  fi

  local pid
  pid="$(cat "${pid_file}")"
  if [[ -z "${pid}" ]]; then
    rm -f "${pid_file}"
    echo "${name} PID file was empty; cleaned up."
    return 0
  fi

  if ! kill -0 "${pid}" >/dev/null 2>&1; then
    rm -f "${pid_file}"
    echo "${name} process ${pid} not running; cleaned up PID file."
    return 0
  fi

  echo "Stopping ${name} process ${pid}..."
  kill -TERM "${pid}" >/dev/null 2>&1 || true

  for _ in {1..20}; do
    if ! kill -0 "${pid}" >/dev/null 2>&1; then
      rm -f "${pid_file}"
      echo "${name} stopped."
      return 0
    fi
    sleep 0.25
  done

  echo "${name} did not stop gracefully; forcing termination..."
  kill -KILL "${pid}" >/dev/null 2>&1 || true
  rm -f "${pid_file}"
  echo "${name} force-stopped."
}

stop_pid_file "${ROOT_DIR}/.winggen.pid" "WingGen"
stop_pid_file "${ROOT_DIR}/.winggen.studio.pid" "WingGen Studio"
