#!/usr/bin/env bash
# Make a copied release live: Python packages, database migrations, static files, then switch
# /opt/netcore/app to it and restart. Run as root:  activate.sh /opt/netcore/releases/<commit>
# To roll back, run it again with an older release folder (migrations are not undone).
set -euo pipefail

RELEASE="$1"
VENV=/opt/netcore/venv
as_netcore() { runuser -u netcore -- "$@"; }

ln -sfn /etc/netcore/netcore.env "$RELEASE/.env"
ln -sfn /var/lib/netcore/var "$RELEASE/var"

[[ -x $VENV/bin/python ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --disable-pip-version-check -r "$RELEASE/requirements.txt"

if [[ -f /var/lib/netcore/db.sqlite3 ]]; then
  echo "Backing up the database before migrating"
  /usr/local/sbin/netcore-backup pre-deploy
fi
as_netcore "$VENV/bin/python" "$RELEASE/manage.py" migrate --no-input
"$VENV/bin/python" "$RELEASE/manage.py" collectstatic --no-input --clear -v 0
as_netcore "$VENV/bin/python" "$RELEASE/manage.py" check --deploy --fail-level WARNING

ln -sfn "$RELEASE" /opt/netcore/app
systemctl restart netcore-web netcore-monitor

for _ in $(seq 1 20); do
  code="$(curl -sk -o /dev/null -w '%{http_code}' https://127.0.0.1/health/ || true)"
  [[ $code == 200 ]] && break
  sleep 1
done
echo "Health check: HTTP $code ($(curl -sk https://127.0.0.1/health/))"

# Keep the five newest releases.
ls -1dt /opt/netcore/releases/*/ | tail -n +6 | while read -r old; do
  [[ "$(readlink -f /opt/netcore/app)/" == "$old" ]] || rm -rf "$old"
done
echo "Live: $(basename "$RELEASE")"
