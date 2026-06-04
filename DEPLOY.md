# Deploying ascii2drawio to Cloud Run

Single container: a multi-stage `Dockerfile` builds the React/Vite frontend, then
serves it (and the API) from FastAPI/uvicorn. The Gemini key is injected as an
env var from Secret Manager — it never ships in the image.

## Verify locally first

```sh
docker build -t ascii2drawio:local .
docker run --rm -p 8080:8080 \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \   # optional; omit to disable AI enhance
  ascii2drawio:local
# open http://127.0.0.1:8080
```

## One-time GCP setup

```sh
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com

# Store the Gemini API key as a secret
printf '%s' "$GEMINI_API_KEY" | gcloud secrets create GEMINI_API_KEY --data-file=-
# (later rotations: `gcloud secrets versions add GEMINI_API_KEY --data-file=-`)

# Let the Cloud Run runtime service account READ the secret. Cloud Run uses the
# project's default compute SA unless you set one. Without this you get:
#   "Permission denied on secret ... roles/secretmanager.secretAccessor"
PROJECT_NUMBER=$(gcloud projects describe "$(gcloud config get-value project)" \
  --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding GEMINI_API_KEY \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

## Deploy (build from source — uses our Dockerfile)

```sh
gcloud run deploy ascii2drawio \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets GEMINI_API_KEY=GEMINI_API_KEY:latest \
  --min-instances 0 \
  --max-instances 5 \
  --concurrency 40 \
  --cpu 1 \
  --memory 512Mi \
  --timeout 60
```

- **`--min-instances 0`** — scale to zero (cheapest; first request after idle pays a cold start).
- **`--concurrency 40`** — requests per instance. The in-process AI limiter caps LLM calls at 4 concurrent / 10 per IP per 60s (`server/limits.py`); these are per-instance, so they loosen as Cloud Run scales out.
- **`--timeout 60`** — request deadline; the Gemini call itself has a 30s per-call timeout.
- Drop `--allow-unauthenticated` if you want the service private (IAM-gated).

## Notes

- No key configured → the app still runs; `GET /api/health` reports `"llm": false`
  and the "Enhance with AI" button is disabled. `/api/convert` (deterministic)
  works regardless.
- Logs are single-line JSON on stdout (`event`, `mode`, sizes, `ms`) and show up
  structured in Cloud Logging. Diagram text is never logged.
- To deploy a prebuilt image instead of `--source .`:
  ```sh
  REGION=us-central1; PROJECT=$(gcloud config get-value project)
  AR=$REGION-docker.pkg.dev/$PROJECT/web/ascii2drawio:latest
  gcloud artifacts repositories create web --repository-format=docker --location=$REGION 2>/dev/null || true
  gcloud auth configure-docker $REGION-docker.pkg.dev
  docker build -t $AR . && docker push $AR
  gcloud run deploy ascii2drawio --image $AR --region $REGION \
    --allow-unauthenticated --set-secrets GEMINI_API_KEY=GEMINI_API_KEY:latest
  ```
