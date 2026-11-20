#!/usr/bin/env bash
# Runs ON the EC2 instance. The pipeline sends it through AWS Systems Manager
# (SSM) with these variables set in front of it:
#   BUCKET      S3 bucket that holds the builds
#   AWS_REGION  region of the bucket
# Optional: BUILD_KEY (default builds/latest.zip), APP_ROOT (default /opt/docflow)
#
# It downloads the latest build from S3, installs it as a new release, switches
# the service to it and checks /health. If the new release is not healthy it
# switches back to the previous one.
set -euo pipefail
: "${BUCKET:?BUCKET is not set}" "${AWS_REGION:?AWS_REGION is not set}"
ROOT="${APP_ROOT:-/opt/docflow}"
KEY="${BUILD_KEY:-builds/latest.zip}"
HEALTH_URL="${HEALTH_URL:-http://localhost/health}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Downloading s3://$BUCKET/$KEY"
aws s3 cp "s3://$BUCKET/$KEY" "$WORK/build.zip" --region "$AWS_REGION"

REL="$ROOT/releases/$(date +%Y%m%d%H%M%S)"
mkdir -p "$REL"
unzip -q "$WORK/build.zip" -d "$REL"
python3 -m venv "$REL/venv"
"$REL/venv/bin/pip" install --quiet -r "$REL/requirements.txt"
chown -R docflow:docflow "$REL"

PREVIOUS="$(readlink -f "$ROOT/current" 2>/dev/null || true)"

switch_to() {
  ln -sfn "$1" "$ROOT/current"
  systemctl restart docflow
}

wait_healthy() {
  for _ in $(seq 1 20); do
    if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

switch_to "$REL"
if wait_healthy; then
  echo "Deployed version $(cat "$REL/VERSION") ($REL) and it is healthy."
  # keep the 3 newest releases
  # shellcheck disable=SC2012  # release names are timestamps
  ls -1dt "$ROOT"/releases/* | tail -n +4 | xargs -r rm -rf
  exit 0
fi

echo "New release did not become healthy. Service log:"
journalctl -u docflow -n 40 --no-pager || true
if [ -n "$PREVIOUS" ] && [ "$PREVIOUS" != "$REL" ]; then
  echo "Rolling back to $PREVIOUS"
  switch_to "$PREVIOUS"
  wait_healthy && echo "Rollback is healthy." || echo "Rollback is also unhealthy."
fi
exit 1
