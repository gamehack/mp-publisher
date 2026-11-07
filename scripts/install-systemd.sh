#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer as root" >&2
  exit 2
fi
if [[ ! -f /opt/mp-publisher/compose.yaml ]]; then
  echo "Expected checkout at /opt/mp-publisher" >&2
  exit 2
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required" >&2
  exit 2
fi
if [[ "$(command -v docker)" != /usr/bin/docker ]]; then
  echo "systemd units expect /usr/bin/docker; review the host path first" >&2
  exit 2
fi

for file in /opt/mp-publisher/systemd/*.service /opt/mp-publisher/systemd/*.timer; do
  install -m 0644 "$file" "/etc/systemd/system/$(basename "$file")"
done
systemctl daemon-reload
echo "mp-publisher units installed. Timers intentionally NOT enabled."
