#!/usr/bin/env bash
# Deploy Blackline to one always-on GCE VM with a persistent disk.
#
# Why a VM and not Cloud Run: Socket Mode needs one process that stays connected, and the
# SQLite queue needs a disk that survives restarts. Cloud Run scales to zero and wipes its
# disk. Moving the queue to Pub/Sub (src/blackline/jobqueue.py) would make Cloud Run work.
#
# Usage:  PROJECT=my-gcp-project ./deploy/gce.sh          first deploy
#         PROJECT=my-gcp-project ./deploy/gce.sh update   new image on the same VM
set -euo pipefail

PROJECT=${PROJECT:?set PROJECT to your GCP project id}
REGION=${REGION:-us-east1}
ZONE=${ZONE:-us-east1-b}
REPO=${REPO:-blackline}
VM=${VM:-blackline}
TAG=$(git rev-parse --short HEAD)
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/blackline:$TAG"

gcloud config set project "$PROJECT" >/dev/null
gcloud services enable artifactregistry.googleapis.com cloudbuild.googleapis.com compute.googleapis.com
gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 \
  || gcloud artifacts repositories create "$REPO" --repository-format=docker --location "$REGION"
gcloud builds submit --tag "$IMAGE" .

if [ "${1:-}" = "update" ]; then
  gcloud compute instances update-container "$VM" --zone "$ZONE" --container-image "$IMAGE"
  exit 0
fi

gcloud compute disks describe blackline-data --zone "$ZONE" >/dev/null 2>&1 \
  || gcloud compute disks create blackline-data --size 10GB --zone "$ZONE"

# only the variables the app needs; tokens end up in VM metadata, move them to
# Secret Manager before real customer use
ENVFILE=$(mktemp)
trap 'rm -f "$ENVFILE"' EXIT
grep -E '^(SLACK_|OPENROUTER_|TIER1_|WORKERS|DEEP_WORKERS|AUDIT_HMAC_KEY)' .env > "$ENVFILE"

gcloud compute instances create-with-container "$VM" \
  --zone "$ZONE" \
  --machine-type e2-small \
  --container-image "$IMAGE" \
  --container-env-file "$ENVFILE" \
  --container-env QUEUE_DB=/data/blackline.db,AUDIT_FILE=/data/audit.jsonl,LOG_FORMAT=json \
  --disk name=blackline-data,device-name=blackline-data,mode=rw,boot=no,auto-delete=no \
  --container-mount-disk mount-path=/data,name=blackline-data,mode=rw \
  --container-restart-policy always

echo "Deployed $IMAGE. Logs: gcloud compute ssh $VM --zone $ZONE -- docker logs -f \$(docker ps -q | head -1)"
