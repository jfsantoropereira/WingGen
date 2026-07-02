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

  echo "Stopping ${name} process group ${pid}..."
  # Launchers like `conda run` wrap the real server in a child process, so
  # signal the whole process group (started with setsid/job control).
  kill -TERM -- "-${pid}" >/dev/null 2>&1 || kill -TERM "${pid}" >/dev/null 2>&1 || true

  for _ in {1..20}; do
    if ! kill -0 "${pid}" >/dev/null 2>&1; then
      rm -f "${pid_file}"
      echo "${name} stopped."
      return 0
    fi
    sleep 0.25
  done

  echo "${name} did not stop gracefully; forcing termination..."
  kill -KILL -- "-${pid}" >/dev/null 2>&1 || kill -KILL "${pid}" >/dev/null 2>&1 || true
  rm -f "${pid_file}"
  echo "${name} force-stopped."
}

kill_port_listeners() {
  # Backstop: clear orphaned listeners on the studio port (children that
  # detached from the recorded PID's process group).
  local port="$1"
  local pids
  pids="$(lsof -nP -t -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    echo "Stopping orphaned listener(s) on port ${port}: ${pids}"
    kill -TERM ${pids} >/dev/null 2>&1 || true
    sleep 1
    pids="$(lsof -nP -t -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
    [[ -n "${pids}" ]] && kill -KILL ${pids} >/dev/null 2>&1 || true
  fi
}

stop_pid_file "${ROOT_DIR}/.winggen.pid" "WingGen"
stop_pid_file "${ROOT_DIR}/.winggen.studio.pid" "WingGen Studio"

# Studio port: [studio].port from the default config, else 8151.
STUDIO_PORT="$(awk '/^\[studio\]/{s=1;next}/^\[/{s=0}s&&/^port *=/{print $3}' \
  "${ROOT_DIR}/configs/default_wing.toml" 2>/dev/null | head -1)"
kill_port_listeners "${STUDIO_PORT:-8151}"
