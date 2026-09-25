#!/usr/bin/env bash
# Deploy Cotorra to Google Cloud Run.
#
#   ./deploy/cloudrun.sh my-gcp-project
#
# Gives you a public HTTPS URL in a few minutes. On Cloud Run the container's
# service account authenticates to Vertex AI by itself, so there is no API key
# to set, rotate or leak.
#
# Cost: a single always-on instance is roughly US$0.10-0.15/hour, plus the
# model calls. Delete the service when you are done (last line of this file).
set -euo pipefail

PROJECT="${1:-${GCP_PROJECT:-}}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-cotorra}"

if [[ -z "$PROJECT" ]]; then
  echo "usage: $0 <gcp-project-id>" >&2
  exit 2
fi

echo "==> project=$PROJECT region=$REGION service=$SERVICE"

gcloud config set project "$PROJECT"
gcloud services enable run.googleapis.com aiplatform.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com

# The service account the container runs as. Cloud Run injects credentials for
# it automatically, which is why no GEMINI_API_KEY appears anywhere below.
SA="cotorra-run@${PROJECT}.iam.gserviceaccount.com"
if ! gcloud iam service-accounts describe "$SA" >/dev/null 2>&1; then
  gcloud iam service-accounts create cotorra-run --display-name "Cotorra on Cloud Run"
fi
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member "serviceAccount:${SA}" \
  --role roles/aiplatform.user \
  --condition=None >/dev/null

# Pick an admin token so the control API is not open to the internet.
ADMIN_TOKEN="${ADMIN_TOKEN:-$(head -c 18 /dev/urandom | base64 | tr -d '/+=' )}"

gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --service-account "$SA" \
  --allow-unauthenticated \
  --port 8080 \
  --memory 2Gi \
  --cpu 2 \
  `# Workers are background subprocesses. Without always-allocated CPU, Cloud` \
  `# Run throttles them to near zero between HTTP requests and captions stop.` \
  --no-cpu-throttling \
  `# One instance only. Workers and viewers must share a process unless you` \
  `# also run Redis — see REDIS_URL in docs/deploy.md.` \
  --min-instances 1 \
  --max-instances 1 \
  `# A talk can hold a websocket open for an hour; 3600 s is the maximum.` \
  --timeout 3600 \
  --set-env-vars "COTORRA_ENGINE=gemini,GEMINI_USE_VERTEX=true,GCP_PROJECT=${PROJECT},GCP_LOCATION=${REGION},GEMINI_MODEL=${GEMINI_MODEL:-gemini-2.5-flash},ADMIN_TOKEN=${ADMIN_TOKEN},WORKER_TOKEN=${WORKER_TOKEN:-$(head -c 12 /dev/urandom | base64 | tr -d '/+=')},MAX_LOCAL_WORKERS=6"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')"

cat <<EOF

==> Cotorra is live

  Audience   $URL/
  Ops panel  $URL/ops?token=$ADMIN_TOKEN
  Health     $URL/api/health

  Admin token: $ADMIN_TOKEN     (write it down, it is not stored anywhere)

The bundled manifest starts the demo stages on the synthetic sample. To caption
something real, POST a session:

  curl -X POST $URL/api/sessions \\
    -H 'X-Cotorra-Token: $ADMIN_TOKEN' -H 'Content-Type: application/json' \\
    -d '{"id":"stage-1","title":"Keynote","source":"https://your-cdn/stage1.m3u8",
         "source_lang":"auto","targets":["es","en"],"engine":"gemini"}'
  curl -X POST $URL/api/sessions/stage-1/start -H 'X-Cotorra-Token: $ADMIN_TOKEN'

When you are finished, stop paying for it:

  gcloud run services delete $SERVICE --region $REGION

EOF
