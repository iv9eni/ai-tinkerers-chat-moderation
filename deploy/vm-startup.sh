#!/bin/bash
# Runs as root on every boot of the Container-Optimized OS VM.
# Mounts the persistent disk, then runs the Blackline image with Docker. Docker restarts it
# after a crash; this script restarts it after a reboot or an image update.
set -euo pipefail

meta() {
  curl -sf -H "Metadata-Flavor: Google" \
    "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"
}

IMAGE=$(meta blackline-image)
REGISTRY=${IMAGE%%/*}
STATE=/var/lib/blackline
DEV=/dev/disk/by-id/google-blackline-data
MNT=/mnt/disks/blackline-data

# persistent disk for the queue and the audit log, formatted on first boot only
mkdir -p "$MNT" "$STATE/docker"
if ! blkid "$DEV" >/dev/null 2>&1; then
  mkfs.ext4 -m 0 -F -E lazy_itable_init=0,lazy_journal_init=0,discard "$DEV"
fi
mountpoint -q "$MNT" || mount -o discard,defaults "$DEV" "$MNT"

# settings that are not secret; tokens come from Secret Manager inside the app
meta blackline-env > "$STATE/app.env" || true
echo "SECRETS_PROJECT=$(meta blackline-project)" >> "$STATE/app.env"

# pull from Artifact Registry with the VM's service account
printf '{"credHelpers": {"%s": "gcr"}}\n' "$REGISTRY" > "$STATE/docker/config.json"
docker --config "$STATE/docker" pull "$IMAGE"

docker rm -f blackline >/dev/null 2>&1 || true
docker --config "$STATE/docker" run -d \
  --name blackline \
  --restart always \
  --network host \
  --stop-timeout 20 \
  --log-driver gcplogs \
  --env-file "$STATE/app.env" \
  -e QUEUE_DB=/data/blackline.db \
  -e AUDIT_FILE=/data/audit.jsonl \
  -e LOG_FORMAT=json \
  -v "$MNT:/data" \
  "$IMAGE"
