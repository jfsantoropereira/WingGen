#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${ROOT_DIR}/.winggen.pid"

if [[ ! -f "${PID_FILE}" ]]; then
  echo "No running WingGen instance found (missing ${PID_FILE})."
  exit 0
fi

pid="$(cat "${PID_FILE}")"
if [[ -z "${pid}" ]]; then
  rm -f "${PID_FILE}"
  echo "PID file was empty; cleaned up."
  exit 0
fi

if ! kill -0 "${pid}" >/dev/null 2>&1; then
  rm -f "${PID_FILE}"
  echo "Process ${pid} not running; cleaned up PID file."
  exit 0
fi

echo "Stopping WingGen process ${pid}..."
kill -TERM "${pid}" >/dev/null 2>&1 || true

for _ in {1..20}; do
  if ! kill -0 "${pid}" >/dev/null 2>&1; then
    rm -f "${PID_FILE}"
    echo "WingGen stopped."
    exit 0
  fi
  sleep 0.25
done

echo "Process did not stop gracefully; forcing termination..."
kill -KILL "${pid}" >/dev/null 2>&1 || true
rm -f "${PID_FILE}"
echo "WingGen force-stopped."
