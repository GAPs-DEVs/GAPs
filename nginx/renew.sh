#!/bin/bash
# Run as root on the host; containers do not receive access to the Docker socket.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

# Prevent concurrent renewal jobs; the lock is released on exit.
exec 9>/run/lock/guitar-practice-certbot.lock
flock -n 9 || exit 0

# Reuse saved IP/webroot settings. Certbot renews only when necessary.
# Always reload after success, also recovering from a prior reload failure.
docker compose --profile tools run --rm certbot renew --non-interactive
docker compose exec -T nginx nginx -t
docker compose exec -T nginx nginx -s reload
