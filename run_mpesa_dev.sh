#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-8000}"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}"
ENV_FILE="$PROJECT_DIR/.env"
TUNNEL_LOG="$(mktemp)"
TUNNEL_PID=""
SERVER_PID=""

cleanup() {
    local status=$?
    trap - EXIT INT TERM
    if [[ -n "$SERVER_PID" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    if [[ -n "$TUNNEL_PID" ]]; then
        kill "$TUNNEL_PID" 2>/dev/null || true
        wait "$TUNNEL_PID" 2>/dev/null || true
    fi
    rm -f "$TUNNEL_LOG"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if ! command -v cloudflared >/dev/null 2>&1; then
    echo "cloudflared is not installed or is not on PATH." >&2
    echo "Install it using Cloudflare's instructions: https://developers.cloudflare.com/tunnel/install/" >&2
    exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Python environment not found at: $PYTHON_BIN" >&2
    echo "Create it and install dependencies with:" >&2
    echo "  python3 -m venv .venv && ./.venv/bin/python -m pip install -r requirements.txt" >&2
    exit 1
fi

if ! "$PYTHON_BIN" -c 'import django, dotenv' >/dev/null 2>&1; then
    echo "Django or python-dotenv is missing from $PYTHON_BIN." >&2
    echo "Install project dependencies with: ./.venv/bin/python -m pip install -r requirements.txt" >&2
    exit 1
fi

echo "Starting Cloudflare Quick Tunnel for http://127.0.0.1:$PORT ..."
cloudflared tunnel --url "http://127.0.0.1:$PORT" >"$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!

TUNNEL_URL=""
for _ in $(seq 1 60); do
    TUNNEL_URL="$(grep -Eo 'https://[[:alnum:]-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -n 1 || true)"
    if [[ -n "$TUNNEL_URL" ]]; then
        break
    fi
    if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
        echo "cloudflared stopped before creating a tunnel:" >&2
        cat "$TUNNEL_LOG" >&2
        exit 1
    fi
    sleep 1
done

if [[ -z "$TUNNEL_URL" ]]; then
    echo "Timed out waiting for Cloudflare to provide a trycloudflare.com URL:" >&2
    cat "$TUNNEL_LOG" >&2
    exit 1
fi

# Update only these keys, preserving Daraja credentials and all other .env entries.
CALLBACK_TOKEN="$(python3 - "$ENV_FILE" "$TUNNEL_URL" <<'PY'
from pathlib import Path
import os
import re
import secrets
import sys

env_path = Path(sys.argv[1])
base_url = sys.argv[2]
lines = env_path.read_text().splitlines() if env_path.exists() else []
token = ""
for line in lines:
    match = re.match(r"^\s*DARAJA_CALLBACK_TOKEN\s*=\s*(.*?)\s*$", line)
    if match:
        token = match.group(1).strip("\"'")
        break
if not token:
    token = secrets.token_urlsafe(32)

updates = {
    "PUBLIC_BASE_URL": base_url,
    "DARAJA_CALLBACK_TOKEN": token,
}
seen = set()
result = []
for line in lines:
    match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
    key = match.group(1) if match else None
    if key in updates:
        if key not in seen:
            result.append(f"{key}={updates[key]}")
            seen.add(key)
    else:
        result.append(line)
for key, value in updates.items():
    if key not in seen:
        result.append(f"{key}={value}")

mode = env_path.stat().st_mode & 0o777 if env_path.exists() else 0o600
env_path.write_text("\n".join(result).rstrip() + "\n")
os.chmod(env_path, mode)
print(token)
PY
)"

export PUBLIC_BASE_URL="$TUNNEL_URL"
export DARAJA_CALLBACK_TOKEN="$CALLBACK_TOKEN"

echo "Tunnel URL: $TUNNEL_URL"
echo "Updated PUBLIC_BASE_URL in .env; existing Daraja callback token was preserved or generated."
echo "Starting Django on http://127.0.0.1:$PORT (press Ctrl+C to stop both processes)."
cd "$PROJECT_DIR"
"$PYTHON_BIN" manage.py runserver "127.0.0.1:$PORT" --noreload &
SERVER_PID=$!
wait "$SERVER_PID"
