#!/usr/bin/env bash
# Deploy Blackline to one always-on GCE VM with a persistent disk.
#
# Why a VM and not Cloud Run: Socket Mode needs one process that stays connected, and the
# SQLite queue needs a disk that survives restarts. Cloud Run scales to zero and wipes its
# disk. Moving the queue to Pub/Sub (src/blackline/jobqueue.py) would make Cloud Run work.
#
# Tokens come from Secret Manager, not from the VM's configuration. Upload them first:
#   PROJECT=my-gcp-project ./deploy/secrets.sh
#
# Usage:  PROJECT=my-gcp-project ./deploy/gce.sh          first deploy
#         PROJECT=my-gcp-project ./deploy/gce.sh update   new image on the same VM
set -euo pipefail
GCLOUD=${GCLOUD:-gcloud}

PROJECT=${PROJECT:?set PROJECT to your GCP project id}
REGION=${REGION:-us-east1}
ZONE=${ZONE:-us-east1-b}
REPO=${REPO:-blackline}
VM=${VM:-blackline}
TAG=$(git rev-parse --short HEAD)
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/blackline:$TAG"

"$GCLOUD" config set project "$PROJECT" >/dev/null
"$GCLOUD" services enable artifactregistry.googleapis.com cloudbuild.googleapis.com compute.googleapis.com
"$GCLOUD" artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 \
  || "$GCLOUD" artifacts repositories create "$REPO" --repository-format=docker --location "$REGION"
"$GCLOUD" builds submit --tag "$IMAGE" .

if [ "${1:-}" = "update" ]; then
  "$GCLOUD" compute instances update-container "$VM" --zone "$ZONE" --container-image "$IMAGE"
  exit 0
fi

"$GCLOUD" compute disks describe blackline-data --zone "$ZONE" >/dev/null 2>&1 \
  || "$GCLOUD" compute disks create blackline-data --size 10GB --zone "$ZONE"

# the VM's default service account may read exactly these secrets and nothing else
NUMBER=$("$GCLOUD" projects describe "$PROJECT" --format="value(projectNumber)")
SA="${NUMBER}-compute@developer.gserviceaccount.com"
for NAME in SLACK_BOT_TOKEN SLACK_APP_TOKEN SLACK_USER_TOKEN OPENROUTER_API_KEY AUDIT_HMAC_KEY; do
  "$GCLOUD" secrets add-iam-policy-binding "$NAME" --project "$PROJECT" \
    --member "serviceAccount:$SA" --role roles/secretmanager.secretAccessor >/dev/null
done

# only settings that are not secret go in the VM configuration
ENVFILE=$(mktemp)
trap 'rm -f "$ENVFILE"' EXIT
grep -E '^(TIER1_|WORKERS|DEEP_WORKERS)' .env > "$ENVFILE" || true
echo "SECRETS_PROJECT=$PROJECT" >> "$ENVFILE"

"$GCLOUD" compute instances create-with-container "$VM" \
  --zone "$ZONE" \
  --machine-type e2-small \
  --scopes cloud-platform \
  --container-image "$IMAGE" \
  --container-env-file "$ENVFILE" \
  --container-env QUEUE_DB=/data/blackline.db,AUDIT_FILE=/data/audit.jsonl,LOG_FORMAT=json \
  --disk name=blackline-data,device-name=blackline-data,mode=rw,boot=no,auto-delete=no \
  --container-mount-disk mount-path=/data,name=blackline-data,mode=rw \
  --container-restart-policy always

echo "Deployed $IMAGE. Logs: "$GCLOUD" compute ssh $VM --zone $ZONE -- docker logs -f \$(docker ps -q | head -1)"
