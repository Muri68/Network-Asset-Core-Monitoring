#!/usr/bin/env bash
# One-time server preparation (Ubuntu/Debian, run as root). Safe to run again: it never replaces
# the settings file, the encryption key, the certificate or the database once they exist.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ADDRESS="${NETCORE_ADDRESS:-$(hostname -I | awk '{print $1}')}"
ENV_FILE=/etc/netcore/netcore.env
TLS=/etc/netcore/tls

need=()
for pkg in nginx python3-venv sqlite3 openssl; do
  dpkg -s "$pkg" >/dev/null 2>&1 || need+=("$pkg")
done
if ((${#need[@]})); then
  echo "Installing ${need[*]}"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${need[@]}" >/dev/null
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' || {
  echo "NetCore needs Python 3.12 or newer; this server has $(python3 --version)." >&2
  exit 1
}

id netcore >/dev/null 2>&1 || useradd --system --home /var/lib/netcore --shell /usr/sbin/nologin netcore
usermod -aG netcore www-data  # Nginx reaches Gunicorn's socket in /run/netcore
install -d -o root -g root -m 755 /opt/netcore /opt/netcore/releases
install -d -o netcore -g netcore -m 750 /var/lib/netcore /var/lib/netcore/var
install -d -o root -g www-data -m 755 /var/lib/netcore/static
install -d -o root -g netcore -m 750 /etc/netcore
install -d -o root -g root -m 700 /var/backups/netcore

if [[ ! -f $ENV_FILE ]]; then
  echo "Writing $ENV_FILE with a new secret key and encryption key"
  secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
  fernet="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
  umask 027
  cat >"$ENV_FILE" <<EOF
# NetCore production settings. Keep an OFFLINE copy of NETCORE_ENCRYPTION_KEYS:
# without it the vault passwords and the Telegram bot token cannot be decrypted.
DJANGO_SECRET_KEY=$secret
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=$ADDRESS,localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=https://$ADDRESS
DJANGO_TIME_ZONE=Africa/Lagos
SQLITE_PATH=/var/lib/netcore/db.sqlite3
NETCORE_STATIC_ROOT=/var/lib/netcore/static
NETCORE_BEHIND_PROXY=1
NETCORE_IDLE_TIMEOUT_MINUTES=30
NETCORE_ENCRYPTION_KEYS=$fernet
EOF
  chown root:netcore "$ENV_FILE"
fi

if [[ ! -f $TLS/netcore.crt ]]; then
  echo "Creating a self-signed certificate for $ADDRESS (valid 10 years)"
  install -d -o root -g root -m 755 "$TLS"
  openssl req -x509 -newkey rsa:3072 -sha256 -days 3650 -nodes -subj "/CN=NetCore $ADDRESS/O=NetCore" \
    -addext "subjectAltName=IP:$ADDRESS" -keyout "$TLS/netcore.key" -out "$TLS/netcore.crt" 2>/dev/null
  chmod 600 "$TLS/netcore.key"
fi

sed "s/__ADDRESS__/$ADDRESS/g" "$HERE/nginx.conf" >/etc/nginx/sites-available/netcore
ln -sf /etc/nginx/sites-available/netcore /etc/nginx/sites-enabled/netcore
rm -f /etc/nginx/sites-enabled/default
nginx -t -q
systemctl enable -q nginx
systemctl restart nginx  # restart, not reload: picks up www-data joining the netcore group

install -m 644 "$HERE"/netcore-web.service "$HERE"/netcore-monitor.service "$HERE"/netcore-backup.service \
  "$HERE"/netcore-backup.timer /etc/systemd/system/
install -m 755 "$HERE/netcore-backup" /usr/local/sbin/netcore-backup
install -m 755 "$HERE/netcore-manage" /usr/local/bin/netcore-manage
systemctl daemon-reload
systemctl enable -q netcore-web netcore-monitor netcore-backup.timer
systemctl start netcore-backup.timer

# Unprivileged ping (icmplib) for the checker's group; most Ubuntu releases already allow every group.
echo "net.ipv4.ping_group_range = 0 2147483647" >/etc/sysctl.d/60-netcore-ping.conf
sysctl -q -p /etc/sysctl.d/60-netcore-ping.conf

# Firewall: if ufw is already on, let LAN addresses reach the web pages. It is never switched on here,
# because the server may run other tools (LibreNMS, Uptime Kuma…) whose ports it would block.
if ufw status | grep -q "Status: active"; then
  for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16; do
    for port in 80 443; do ufw allow from "$net" to any port "$port" proto tcp >/dev/null; done
  done
  echo "Firewall: ports 80 and 443 open to LAN addresses"
fi
