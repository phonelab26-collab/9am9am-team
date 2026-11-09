#!/usr/bin/env bash
# Runtime smoke test: starts the built server with dummy provider keys and checks
# health, dev login, hashed sessions, security headers and the OAuth attempt guard.
set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${SMOKE_PORT:-8799}"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/9am9am-smoke.XXXXXX")"
PID=""

cleanup() {
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

if [ ! -f server/dist/index.js ]; then
  echo "smoke: building..."
  npm run build >/dev/null
fi

export XAI_API_KEY=smoke-xai
export COMPOSIO_API_KEY=smoke-composio
export ORGO_API_KEY=smoke-orgo
export ORGO_WORKSPACE_ID=smoke-workspace
export SESSION_SECRET=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
export PORT
export APP_URL="http://127.0.0.1:${PORT}"
export SERVER_URL="http://127.0.0.1:${PORT}"
export DEV_LOGIN=1
export DB_PATH="${TMP}/9am9am.db"

node server/dist/index.js >"${TMP}/server.log" 2>&1 &
PID=$!

for _ in $(seq 1 100); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/health" >"${TMP}/health.json" 2>/dev/null; then
    break
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "smoke: server exited early" >&2
    cat "${TMP}/server.log" >&2
    exit 1
  fi
  sleep 0.1
done

grep -q '"ok":true' "${TMP}/health.json"

HEADERS="${TMP}/headers.txt"
curl -fsS -D "$HEADERS" -o /dev/null "http://127.0.0.1:${PORT}/api/health"
grep -qi '^x-content-type-options: nosniff' "$HEADERS"
grep -qi '^x-frame-options: DENY' "$HEADERS"

COOKIE_JAR="${TMP}/cookies.txt"
curl -fsS -c "$COOKIE_JAR" -H 'content-type: application/json' \
  -d '{"email":"smoke@9am9am.test","name":"Smoke"}' \
  "http://127.0.0.1:${PORT}/api/auth/dev" | grep -q '"ok":true'

curl -fsS -b "$COOKIE_JAR" "http://127.0.0.1:${PORT}/api/auth/me" | grep -q 'smoke@9am9am.test'
curl -fsS -b "$COOKIE_JAR" "http://127.0.0.1:${PORT}/api/bots" | grep -q '"bots"'

# The browser holds the raw token; the database must only contain a hash.
TOKEN="$(awk '$6 == "9am9am_session" { print $7 }' "$COOKIE_JAR")"
[ -n "$TOKEN" ]
STORED="$(node -e "const D=require('better-sqlite3');const db=new D(process.argv[1]);const r=db.prepare('SELECT token FROM auth_sessions LIMIT 1').get();console.log(r.token);db.close()" "$DB_PATH")"
[ "$STORED" != "$TOKEN" ]
[ "${#STORED}" -eq 64 ]

# A callback without the browser-bound attempt cookie must fail closed.
REDIRECT="$(curl -sS -o /dev/null -w '%{redirect_url}' "http://127.0.0.1:${PORT}/api/auth/google/callback?attempt=forged")"
case "$REDIRECT" in
  *"login=failed"*) ;;
  *) echo "smoke: OAuth callback without attempt cookie was not rejected" >&2; exit 1 ;;
esac

echo "smoke: ok"
