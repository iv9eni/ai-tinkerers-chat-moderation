#!/usr/bin/env bash
# Copy Blackline's tokens from your local .env into Google Secret Manager.
# Values are piped straight to gcloud; they are never printed or written to another file.
# Running it again adds a new version, which is how you rotate a token.
#
# Usage:  PROJECT=backline-508417 ./deploy/secrets.sh
set -euo pipefail

PROJECT=${PROJECT:?set PROJECT to your GCP project id}
GCLOUD=${GCLOUD:-gcloud}
NAMES="SLACK_BOT_TOKEN SLACK_APP_TOKEN SLACK_USER_TOKEN OPENROUTER_API_KEY AUDIT_HMAC_KEY"

"$GCLOUD" services enable secretmanager.googleapis.com --project "$PROJECT"

for NAME in $NAMES; do
  VALUE=$(grep -E "^${NAME}=" .env | head -1 | cut -d= -f2-)
  if [ -z "$VALUE" ]; then
    echo "skipped $NAME: not set in .env"
    continue
  fi
  if "$GCLOUD" secrets describe "$NAME" --project "$PROJECT" >/dev/null 2>&1; then
    printf '%s' "$VALUE" | "$GCLOUD" secrets versions add "$NAME" --data-file=- --project "$PROJECT" >/dev/null
    echo "updated $NAME (new version)"
  else
    printf '%s' "$VALUE" | "$GCLOUD" secrets create "$NAME" --data-file=- \
      --replication-policy=automatic --project "$PROJECT" >/dev/null
    echo "created $NAME"
  fi
done
unset VALUE
