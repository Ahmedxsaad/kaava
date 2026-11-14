#!/usr/bin/env bash
# The public copy of the app on a VM (2 vCPU and 8 GB are enough: about 220 MB of memory at rest).
#
#   scripts/deploy_vm.sh update    send the last commit, rebuild the image on the VM, restart the app
#   scripts/deploy_vm.sh cache     copy this machine's cache to the VM (indexes, 3D meshes, kept answers)
#   scripts/deploy_vm.sh status    what runs there, and whether the public address answers
#   scripts/deploy_vm.sh logs      the app's last lines
#
# Where the VM is comes from .env.deploy (git-ignored): STOA_VM=user@host, STOA_VM_KEY=path of its ssh key,
# and optionally STOA_HOST (the public host name). The VM holds, under ~/stoa: the code, .env, data/
# (the dataset, fetched there by fetch_dataset.sh), cache/, uploads-public/ and cloudflared/tunnel.json.
# Two containers run there and restart by themselves: blueprints-public (the app, on 127.0.0.1:8000) and
# stoa-tunnel (cloudflared, which brings the public host name to it; no port of the VM is open).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env.deploy ] || { echo "no .env.deploy: set STOA_VM and STOA_VM_KEY in it" >&2; exit 2; }
set -a; . ./.env.deploy; set +a
HOST_NAME=${STOA_HOST:-stoa.ahmedxsaad.me}
SSH=(ssh -i "$STOA_VM_KEY" -o BatchMode=yes -o ConnectTimeout=15 "$STOA_VM")

run_app='sudo docker rm -f blueprints-public >/dev/null 2>&1; sudo docker run -d --name blueprints-public --restart unless-stopped
  --user "$(id -u):$(id -g)" -p 127.0.0.1:8000:8000 -v "$HOME/stoa/data:/app/data:ro" -v "$HOME/stoa/cache:/app/cache"
  -v "$HOME/stoa/uploads-public:/app/uploads" --env-file "$HOME/stoa/.env" -e BLUEPRINTS_UI_BUDGET_EUR=${BLUEPRINTS_UI_BUDGET_EUR:-5}
  -e HOME=/tmp blueprints-talk >/dev/null
  for i in $(seq 1 40); do [ "$(curl -s -o /dev/null --max-time 5 -w "%{http_code}" http://127.0.0.1:8000/api/stats)" = 200 ] && break; sleep 3; done'

status() {
  "${SSH[@]}" 'sudo docker ps --format "{{.Names}}: {{.Status}}"; echo "app on the VM: $(curl -s -o /dev/null --max-time 10 -w "%{http_code}" http://127.0.0.1:8000/api/stats)"; cat ~/stoa/.deployed 2>/dev/null; true'
  echo "public: $(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "https://${HOST_NAME}/api/stats" || true)  https://${HOST_NAME}"
}

case "${1:-status}" in
  update)
    [ -z "$(git status --porcelain --untracked-files=no)" ] || echo "note: uncommitted changes are not sent; the last commit is" >&2
    commit=$(git log -1 --format='%h %s')
    git archive --format=tar HEAD | gzip -1 | "${SSH[@]}" 'tar -xz -C ~/stoa'
    "${SSH[@]}" "cd ~/stoa && sudo docker build -q -t blueprints-talk . >/dev/null && echo 'image built' && $(echo $run_app) && echo 'commit: $commit' > .deployed"
    status ;;
  cache)
    rsync -az --partial --exclude 'tunnel.*' --exclude 'watch.log' -e "ssh -i $STOA_VM_KEY -o BatchMode=yes" cache/ "$STOA_VM:stoa/cache/"
    echo "cache copied" ;;
  status) status ;;
  logs) "${SSH[@]}" 'sudo docker logs --tail 40 blueprints-public 2>&1' ;;
  *) echo "usage: $0 update|cache|status|logs" >&2; exit 2 ;;
esac
