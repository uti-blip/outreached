#!/usr/bin/env bash
# Build and verify the real combined image against disposable TLS PostgreSQL.
# No cloud account, application credentials, or existing data is used.
set -euo pipefail

cd "$(dirname "$0")/.."
for executable in docker openssl python3; do
  command -v "$executable" >/dev/null || { echo "Required executable unavailable: $executable" >&2; exit 1; }
done
docker info >/dev/null

test_root="$(mktemp -d)"
test_id="outreached-free-$(openssl rand -hex 5)"
test_network="$test_id-network"
test_database="$test_id-postgres"
test_volume="$test_id-data"
test_app="$test_id-app"
test_image="$test_id:ci"

cleanup() {
  result=$?
  trap - EXIT
  if [ "$result" -ne 0 ]; then
    docker logs --tail 120 "$test_app" 2>/dev/null || true
    docker logs --tail 80 "$test_database" 2>/dev/null || true
  fi
  docker rm --force "$test_app" "$test_database" >/dev/null 2>&1 || true
  docker network rm "$test_network" >/dev/null 2>&1 || true
  docker volume rm "$test_volume" >/dev/null 2>&1 || true
  docker image rm "$test_image" >/dev/null 2>&1 || true
  rm -rf "$test_root"
  exit "$result"
}
trap cleanup EXIT

owner_password="$(openssl rand -hex 32)"
runtime_password="$(openssl rand -hex 32)"
api_key="$(openssl rand -hex 32)"
secret_key="$(openssl rand -hex 32)"
session_secret="$(openssl rand -hex 32)"
export WORKSPACE_LOGIN_USER="ci-synthetic"
export WORKSPACE_LOGIN_PASSWORD="$(openssl rand -hex 24)"
if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
  for secret in "$owner_password" "$runtime_password" "$api_key" "$secret_key" "$session_secret" "$WORKSPACE_LOGIN_PASSWORD"; do
    echo "::add-mask::$secret"
  done
fi
password_hash="$(python3 - <<'PY'
import hashlib
import os
salt = os.urandom(16)
derived = hashlib.scrypt(os.environ['WORKSPACE_LOGIN_PASSWORD'].encode(), salt=salt, n=16384, r=8, p=1, dklen=64)
print(f'scrypt:16384:8:1:{salt.hex()}:{derived.hex()}')
PY
)"
if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
  echo "::add-mask::$password_hash"
fi

mkdir "$test_root/tls"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -keyout "$test_root/tls/server.key" -out "$test_root/tls/server.crt" \
  -subj '/CN=postgres' -addext 'subjectAltName=DNS:postgres' >/dev/null 2>&1
chmod 644 "$test_root/tls/server.crt"
docker network create "$test_network" >/dev/null
docker volume create "$test_volume" >/dev/null
docker run --detach --name "$test_database" \
  --network "$test_network" --network-alias postgres \
  --mount "type=volume,source=$test_volume,target=/var/lib/postgresql/data" \
  --mount "type=bind,source=$test_root/tls,target=/ci-tls,readonly" \
  --env POSTGRES_DB=workspace --env POSTGRES_USER=workspace_owner \
  --env POSTGRES_PASSWORD="$owner_password" \
  --health-cmd 'pg_isready -U workspace_owner -d workspace' \
  --health-interval 2s --health-timeout 3s --health-retries 30 \
  --entrypoint /bin/sh postgres:17-bookworm -ec '
    cp /ci-tls/server.crt /tmp/server.crt
    cp /ci-tls/server.key /tmp/server.key
    chown postgres:postgres /tmp/server.crt /tmp/server.key
    chmod 600 /tmp/server.key
    exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tmp/server.crt -c ssl_key_file=/tmp/server.key
  ' >/dev/null

for attempt in {1..40}; do
  if [ "$(docker inspect --format '{{.State.Health.Status}}' "$test_database")" = healthy ]; then break; fi
  sleep 2
done
test "$(docker inspect --format '{{.State.Health.Status}}' "$test_database")" = healthy

docker build --file deploy/Dockerfile.free --tag "$test_image" .
certificate_mount="type=bind,source=$test_root/tls/server.crt,target=/certs/server.crt,readonly"
docker run --rm --network "$test_network" --mount "$certificate_mount" \
  --env "WORKSPACE_MIGRATION_DATABASE_URL=postgresql://workspace_owner:$owner_password@postgres:5432/workspace?sslmode=verify-full" \
  --env WORKSPACE_RUNTIME_DB_PASSWORD="$runtime_password" \
  --env WORKSPACE_DATABASE_SSL_ROOT_CERT=/certs/server.crt \
  --entrypoint python "$test_image" scripts/workspace_postgres.py migrate

docker run --detach --name "$test_app" --network "$test_network" \
  --publish 127.0.0.1::10000 --mount "$certificate_mount" \
  --env "WORKSPACE_DATABASE_URL=postgresql://outreached_app:$runtime_password@postgres:5432/workspace?sslmode=verify-full" \
  --env WORKSPACE_DATABASE_SSL_ROOT_CERT=/certs/server.crt \
  --env WORKSPACE_API_KEY="$api_key" --env SECRET_KEY="$secret_key" \
  --env WORKSPACE_LOGIN_USER --env WORKSPACE_PASSWORD_HASH="$password_hash" \
  --env WORKSPACE_SESSION_SECRET="$session_secret" \
  --env WORKSPACE_PUBLIC_ORIGIN=https://workspace.example.test \
  --env COMMERCIAL_LAUNCH_ENABLED=false \
  "$test_image" >/dev/null
address="http://$(docker port "$test_app" 10000/tcp)"

python3 scripts/verify_free_container.py --address "$address" \
  --phase before-restart --state-file "$test_root/state.json"

# The backend is not published. Check its auth barrier inside the actual image.
docker exec "$test_app" python -c '
import urllib.error
import urllib.request
request = urllib.request.Request("http://127.0.0.1:8001/api/workspace", headers={"Host": "workspace.example.test"})
try:
    urllib.request.urlopen(request, timeout=5)
except urllib.error.HTTPError as error:
    assert error.code == 401, "Private backend must reject unauthenticated reads"
else:
    raise AssertionError("Private backend exposed unauthenticated workspace data")
print("PASS private backend authentication")
'

# Restart both processes and PostgreSQL, retaining only the external DB volume.
docker restart "$test_database" >/dev/null
for attempt in {1..40}; do
  if [ "$(docker inspect --format '{{.State.Health.Status}}' "$test_database")" = healthy ]; then break; fi
  sleep 2
done
test "$(docker inspect --format '{{.State.Health.Status}}' "$test_database")" = healthy
docker restart "$test_app" >/dev/null
# An ephemeral host port can be reassigned when Docker restarts the container.
# Resolve the current mapping before probing the restarted application.
address="http://$(docker port "$test_app" 10000/tcp)"
python3 scripts/verify_free_container.py --address "$address" \
  --phase after-restart --state-file "$test_root/state.json"
echo 'PASS combined production image: TLS PostgreSQL, auth, CSRF, business flow and restart persistence'
