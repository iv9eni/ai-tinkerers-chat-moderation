#!/usr/bin/env bash
# Deploy Blackline to one always-on GCE VM with a persistent disk.
#
# The VM runs Container-Optimized OS. deploy/vm-startup.sh mounts the disk and runs the image
# with Docker on every boot. (Google discontinued create-with-container in 2026.)
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
"$GCLOUD" services enable artifactregistry.googleapis.com cloudbuild.googleapis.com \
  compute.googleapis.com secretmanager.googleapis.com logging.googleapis.com
"$GCLOUD" artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 \
  || "$GCLOUD" artifacts repositories create "$REPO" --repository-format=docker --location "$REGION"
# the image is tagged with the commit, so an existing tag means nothing to rebuild
if "$GCLOUD" artifacts docker images describe "$IMAGE" >/dev/null 2>&1; then
  echo "Image $IMAGE already built, skipping Cloud Build"
else
  "$GCLOUD" builds submit --tag "$IMAGE" .
fi

if [ "${1:-}" = "update" ]; then
  "$GCLOUD" compute instances add-metadata "$VM" --zone "$ZONE" --metadata blackline-image="$IMAGE"
  # a clean stop sends SIGTERM to the container; the startup script pulls the new image
  "$GCLOUD" compute instances stop "$VM" --zone "$ZONE"
  "$GCLOUD" compute instances start "$VM" --zone "$ZONE"
  echo "Updated $VM to $IMAGE"
  exit 0
fi

"$GCLOUD" compute disks describe blackline-data --zone "$ZONE" >/dev/null 2>&1 \
  || "$GCLOUD" compute disks create blackline-data --size 10GB --zone "$ZONE"

# the VM's service account: read these secrets, pull this image, write logs, nothing more
NUMBER=$("$GCLOUD" projects describe "$PROJECT" --format="value(projectNumber)")
SA="${NUMBER}-compute@developer.gserviceaccount.com"
for NAME in SLACK_BOT_TOKEN SLACK_APP_TOKEN SLACK_USER_TOKEN OPENROUTER_API_KEY AUDIT_HMAC_KEY; do
  "$GCLOUD" secrets add-iam-policy-binding "$NAME" --project "$PROJECT" \
    --member "serviceAccount:$SA" --role roles/secretmanager.secretAccessor >/dev/null
done
"$GCLOUD" artifacts repositories add-iam-policy-binding "$REPO" --location "$REGION" \
  --member "serviceAccount:$SA" --role roles/artifactregistry.reader >/dev/null
"$GCLOUD" projects add-iam-policy-binding "$PROJECT" --condition=None \
  --member "serviceAccount:$SA" --role roles/logging.logWriter >/dev/null

# only settings that are not secret go in the VM metadata
ENVFILE=$(mktemp)
trap 'rm -f "$ENVFILE"' EXIT
grep -E '^(TIER1_|WORKERS|DEEP_WORKERS)' .env > "$ENVFILE" || true

"$GCLOUD" compute instances create "$VM" \
  --zone "$ZONE" \
  --machine-type e2-small \
  --image-family cos-stable \
  --image-project cos-cloud \
  --scopes cloud-platform \
  --disk name=blackline-data,device-name=blackline-data,mode=rw,boot=no,auto-delete=no \
  --metadata-from-file startup-script=deploy/vm-startup.sh,blackline-env="$ENVFILE" \
  --metadata blackline-image="$IMAGE",blackline-project="$PROJECT"

echo "Deployed $IMAGE to $VM."
echo "Logs:   gcloud logging read 'logName:\"gcplogs-docker-driver\"' --limit 20"
echo "Health: gcloud compute ssh $VM --zone $ZONE --command 'curl -s localhost:8080/healthz'"
