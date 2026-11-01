#!/usr/bin/env bash
# Deploy the committed code (HEAD) to the NetCore server.  Run from the project folder on your PC:
#
#     deploy/deploy.sh                      # to supportadmin@10.255.15.66
#     deploy/deploy.sh root@other-server
#
# Builds the CSS here, copies HEAD to /opt/netcore/releases/<commit> on the server, and runs
# deploy/server/setup.sh there (packages, user, folders, certificate, Nginx, services; safe to repeat)
# and deploy/server/activate.sh (Python packages, migrations, static files, switch, restart).
set -euo pipefail

HOST="${1:-supportadmin@10.255.15.66}"
cd "$(dirname "$0")/.."

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "Commit your changes first: only committed code (HEAD) is deployed." >&2
  exit 1
fi
SHA="$(git rev-parse --short HEAD)"
RELEASE="/opt/netcore/releases/$SHA"

echo "==> Building CSS"
.venv/bin/python manage.py tailwind build >/dev/null

echo "==> Copying $SHA to $HOST:$RELEASE"
BUNDLE="$(mktemp -t netcore-XXXX).tar"
trap 'rm -f "$BUNDLE"' EXIT
git archive --format=tar HEAD >"$BUNDLE"
COPYFILE_DISABLE=1 tar --no-mac-metadata -rf "$BUNDLE" static/css/app.css  # built, not in git
gzip -c "$BUNDLE" | ssh "$HOST" "sudo rm -rf '$RELEASE' && sudo mkdir -p '$RELEASE' && sudo tar -xz --no-same-owner -C '$RELEASE'"

echo "==> Server setup"
ssh "$HOST" "sudo NETCORE_ADDRESS='${HOST#*@}' bash '$RELEASE/deploy/server/setup.sh'"
echo "==> Activating $SHA"
ssh "$HOST" "sudo bash '$RELEASE/deploy/server/activate.sh' '$RELEASE'"
