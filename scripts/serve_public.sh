#!/usr/bin/env bash
# The public copy of the app and its Cloudflare tunnel.
#
#   scripts/serve_public.sh start     start (or restart) the app's container and the tunnel
#   scripts/serve_public.sh update    rebuild the image from the code as it is now, then start
#   scripts/serve_public.sh status    what is running, and whether the public address answers
#   scripts/serve_public.sh tunnel    restart only the tunnel (the app keeps running)
#   scripts/serve_public.sh stop      take the public copy offline
#
# "start" also leaves a watchdog running: every 45 s it asks the public address for the app, and when
# the app answers here but not there twice in a row, it restarts the tunnel (a tunnel that lost its
# connections has been seen to stay down). What it did is in cache/watch.log.
#
# The public copy is the Docker image, so edits to the code do not reach it until "update".
# It needs a named tunnel that already routes the host name to it (made once with
# `cloudflared tunnel create` and `cloudflared tunnel route dns`). Model spend per start of the
# container stops at BLUEPRINTS_UI_BUDGET_EUR (5 if unset).
set -euo pipefail
cd "$(dirname "$0")/.."

NAME=blueprints-public
TUNNEL=${BLUEPRINTS_TUNNEL:-blueprints-talk}
HOST_NAME=${BLUEPRINTS_HOST:-stoa.ahmedxsaad.me}
PORT=8000
BUDGET=${BLUEPRINTS_UI_BUDGET_EUR:-5}

# The tunnel's id, when cache/tunnel.id holds it: run by id, cloudflared needs only the credentials file
# on this machine; run by name, it first asks Cloudflare's API for the id, and fails when that times out.
TARGET=$( [ -s cache/tunnel.id ] && head -1 cache/tunnel.id || echo "$TUNNEL" )

tunnel_pid() { pgrep -f "[c]loudflared tunnel .*run --url http://127.0.0.1:${PORT} " || true; }
watch_pid() { pgrep -f "[s]erve_public.sh watch" || true; }

tunnel_up() {
  for pid in $(tunnel_pid); do kill "$pid" 2>/dev/null || true; done
  sleep 1
  for pid in $(tunnel_pid); do kill -9 "$pid" 2>/dev/null || true; done  # one that hangs while shutting down
  # HTTP/2 rather than QUIC: on some networks the UDP connections of QUIC do not hold.
  (setsid nohup cloudflared tunnel --no-autoupdate --protocol http2 run --url "http://127.0.0.1:${PORT}" "$TARGET" \
    > cache/tunnel.log 2>&1 < /dev/null &)
}

answers() { [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time "$2" "$1" || true)" = "200" ]; }

watch() {
  local misses=0
  while true; do
    sleep 45
    if answers "http://127.0.0.1:${PORT}/api/stats" 10 && ! answers "https://${HOST_NAME}/api/stats" 25; then
      misses=$((misses + 1))
      if [ "$misses" -ge 2 ]; then
        echo "$(date -u +%FT%TZ) the public address does not answer and the app does: restarting the tunnel" >> cache/watch.log
        tunnel_up
        misses=0
        sleep 20
      fi
    else
      misses=0
    fi
  done
}

start() {
  mkdir -p uploads-public cache
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker run -d --name "$NAME" --restart unless-stopped --user "$(id -u):$(id -g)" \
    -p "127.0.0.1:${PORT}:8000" \
    -v "$PWD/data:/app/data:ro" -v "$PWD/cache:/app/cache" -v "$PWD/uploads-public:/app/uploads" \
    --env-file .env -e "BLUEPRINTS_UI_BUDGET_EUR=${BUDGET}" -e HOME=/tmp blueprints-talk >/dev/null
  for _ in $(seq 1 40); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/api/stats")" = "200" ] && break
    sleep 3
  done
  tunnel_up
  for pid in $(watch_pid); do kill "$pid" 2>/dev/null || true; done
  (setsid nohup "$0" watch > /dev/null 2>&1 < /dev/null &)
  sleep 12
  status
}

status() {
  echo "app container : $(docker ps --filter "name=^${NAME}$" --format '{{.Status}}' | head -1 || true)"
  echo "tunnel        : $( [ -n "$(tunnel_pid)" ] && echo running || echo "not running" )"
  echo "watchdog      : $( [ -n "$(watch_pid)" ] && echo running || echo "not running" )"
  echo "local         : $(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:${PORT}/" || true)"
  echo "public        : $(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "https://${HOST_NAME}/" || true)  https://${HOST_NAME}"
}

case "${1:-start}" in
  start) start ;;
  update) docker build -t blueprints-talk . && start ;;
  status) status ;;
  tunnel) tunnel_up; sleep 12; status ;;
  watch) watch ;;
  stop)
    for pid in $(watch_pid); do kill "$pid" 2>/dev/null || true; done
    for pid in $(tunnel_pid); do kill "$pid"; done
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    echo "the public copy is offline" ;;
  *) echo "usage: $0 start|update|status|tunnel|stop" >&2; exit 2 ;;
esac
