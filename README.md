# TuneStack

A production-style, cloud-native music recommendation API built with Python, FastAPI, Docker, Terraform, GKE Autopilot, GitHub Actions, Prometheus, and Grafana.

## Live Demo

▶️ **[Watch the Infrastructure & API Demo on YouTube](https://youtu.be/KzUd22zV6JU)**

> **Note:** The live GKE Autopilot environment has been spun down to save cloud costs, but you can view the full architecture, API usage, and infrastructure deployment in the video demo above. The source code, Terraform configurations, Kubernetes manifests, and CI/CD workflows remain available in this repository.

---

## Architecture and Data Flow

**Data flow:**
1. Developer pushes to `main` on GitHub
2. GitHub Actions authenticates to GCP via Workload Identity Federation (OIDC, no static keys)
3. CI runs pytest, builds Docker image, pushes to Artifact Registry
4. CD applies K8s manifests to GKE — rolling deployment, zero downtime
5. FastAPI pod serves traffic via public LoadBalancer IP
6. Prometheus scrapes app + cluster metrics; Grafana visualizes
7. Secrets are pulled live from Secret Manager into pods via CSI driver

---

## Phase 1 — The Recommender Model

**Dataset:** [TidyTuesday Spotify Songs](https://github.com/rfordatascience/tidytuesday/tree/master/data/2020/2020-01-21) — 32,833 tracks tagged with `playlist_genre` and `playlist_subgenre`.

### Approach

For each track, build a text "soup" combining:
- `playlist_genre` (weight 2)
- `playlist_subgenre` (weight 3 — most specific meaningful signal)
- `track_artist` (weight 1)

Vectorize with scikit-learn's `CountVectorizer`, then rank tracks by cosine similarity.

### Design decisions

**Artist-diversity cap.** Two tracks by the same artist in the same subgenre produce identical vectors and tie at similarity 1.0. Weighting alone doesn't break this tie, so the recommender enforces a max of 2 tracks per artist directly during neighbor selection — preventing "all Drake, all the time" result lists.

**Storage optimization.**
- **Naive approach:** persist full 5,000×5,000 similarity matrix (~200MB dense)
- **What TuneStack does:** save only top-30 neighbors per track (`neighbors.pkl`, ~2.4MB) + track metadata (`tracks.pkl`, ~350KB)
- **Result:** ~85% reduction in cold-start lookup latency, ~99% reduction in artifact size

The full matrix is computed once in memory during the build step, then discarded. Only the compressed neighbor index ships in the container image.

---

## Phase 2 — FastAPI Wrapper

REST API exposes the recommender behind a clean HTTP contract.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Redirects to Swagger UI |
| `GET` | `/health` | Liveness probe — returns `{"status": "ok"}` |
| `GET` | `/recommend` | Returns top-N similar tracks |
| `GET` | `/docs` | Auto-generated interactive Swagger UI |
| `GET` | `/metrics` | Prometheus scrape target (request count, latency histograms, error rates) |

### Design notes

- Case-insensitive matching on both `track_name` and `artist`
- Optional `n` query param (1–20, defaults to 5)
- Returns HTTP 404 with a clear message when a track isn't in the dataset
- Metrics instrumentation via `prometheus-fastapi-instrumentator`

---

## Phase 3 — Docker Containerization

Multi-stage build for a lean production image.

- **Stage 1 (builder):** installs Python dependencies with cache mounts for fast rebuilds
- **Stage 2 (runtime):** copies only the compiled wheels + application code
- Runs as a non-root user (`appuser`)
- Final image size: **~180MB**

Exposes port `8000`; entry point is `uvicorn main:app --host 0.0.0.0 --port 8000`.

---

## Phase 4 — Terraform: GCP Foundation

All infrastructure is defined as code in `infra/`. No click-ops.

### Provisions

- **VPC** with a custom subnet in `us-central1`
- **Artifact Registry** repository for Docker images
- **IAM service accounts** for:
  - GKE nodes
  - GitHub Actions CI/CD (bound via Workload Identity Federation)
  - Workload Identity for in-cluster pods accessing Secret Manager
- **GCP APIs enabled** programmatically:
  - `container.googleapis.com`
  - `artifactregistry.googleapis.com`
  - `secretmanager.googleapis.com`
  - `iamcredentials.googleapis.com`

State is stored locally for this personal project; a production version would use a **GCS backend with state locking** via `terraform_remote_state`.

---

## Phase 5 — Terraform: GKE Autopilot Cluster

**Why Autopilot over Standard:**
- Google manages node provisioning, scaling, security patches, and OS upgrades
- Pay-per-pod pricing — no idle node cost
- Enforces security best practices by default (no privileged pods, mandatory Workload Identity, restricted host networking)
- Fewer knobs to misconfigure — appropriate for a portfolio project

The cluster is defined in `infra/gke.tf` — a single `google_container_cluster` resource with `enable_autopilot = true` and Workload Identity Federation bound to the project's identity namespace (`PROJECT_ID.svc.id.goog`).

---

## Phase 6 — GitHub Actions CI

`.github/workflows/ci.yml` runs on every push and PR.

### Pipeline stages

| Stage | Action |
|---|---|
| 1. Test | `pytest` runs against the FastAPI app |
| 2. Build | Docker multi-stage build |
| 3. Push | Image tagged with commit SHA, pushed to Artifact Registry |

### Authentication

Uses **Workload Identity Federation** — GitHub Actions assumes a GCP service account via OIDC token exchange.

**Zero long-lived service account keys** are stored in the repo, GitHub Secrets, or anywhere else. This is the recommended pattern for GitHub-to-GCP CI/CD and eliminates the largest class of cloud credential leaks.

**Full pipeline runtime: under 4 minutes.**

---

## Phase 7 — Kubernetes Manifests

All manifests live in `k8s/`.

### Included resources

- **Deployment** with 1 replica (autoscales up to 3 via HPA)
- **Service** of type `LoadBalancer` — exposes the app publicly on port 80
- **HorizontalPodAutoscaler** — scales based on CPU utilization (target: 70%)
- **Liveness & readiness probes** — both hit `/health` on port 8000
- **Resource requests & limits** — CPU 100m/500m, memory 256Mi/512Mi (keeps Autopilot billing predictable)

---

## Phase 8 — GitHub Actions CD

Push to `main` → CI passes → CD job triggers automatically.

### CD steps

1. Authenticates to GKE via Workload Identity (no `kubeconfig` file in secrets)
2. Renders the deployment YAML with the new image tag
3. Applies via `kubectl apply -f k8s/`
4. GKE performs a rolling update — old pods drain, new pods start, zero downtime

**End-to-end deploy time (commit → live on the URL): ~4 minutes.**

---

## Phase 9 — Observability (Prometheus + Grafana)

Deployed via the [`kube-prometheus-stack`](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack) Helm chart in the `monitoring` namespace.

### What's scraped

- **Cluster-level:** nodes, pods, kubelet, kube-state-metrics
- **Application-level:** FastAPI `/metrics` endpoint:
  - Request count by endpoint
  - Latency histograms (p50, p95, p99)
  - Error rate by HTTP status code
- **ServiceMonitor** CRDs configured for auto-discovery of scrape targets

### Dashboards

- Cluster health (CPU, memory, pod restarts) — default kube-prometheus-stack dashboards
- Application SLIs (RPS, error rate, latency) — custom-scraped from the FastAPI instrumentation

---

## Phase 10 — Secrets: Secret Manager + Workload Identity

The **CSI Secrets Store driver** is wired to **GCP Secret Manager** via **GKE Workload Identity**.

### Verified end-to-end

- A value is stored in Secret Manager
- Mounted live into the running pod's filesystem via the CSI driver
- Available as a file inside the pod at a defined mount path
- **No static keys, no downloaded credential files, no service account JSON** anywhere in the chain

### Known limitation: Grafana admin credential rotation

Wiring the CSI mechanism specifically into Grafana's `admin.existingSecret` was **evaluated and deliberately not pursued further**.

`kube-prometheus-stack` / Grafana has a **long-standing, still-open upstream issue** (tracked across multiple GitHub issues since 2022): Grafana caches the admin password in its own internal SQLite database at first boot and doesn't reliably re-read it afterward, even when the pod's environment variable is confirmed correctly updated by the CSI driver.

Forcing this further would require resetting Grafana's internal database — for a result that's genuinely hard to verify as *the fix* rather than *the original cached value being overwritten*.

**The platform-level mechanism — Secret Manager plus Workload Identity — is what's being demonstrated here, and it's proven end-to-end via the test pod, independent of this one chart's limitation.**

This is called out explicitly rather than hidden because knowing when *not* to force a workaround is part of engineering judgment.

---

## Running Locally

### Option A — Python virtualenv

```bash
git clone https://github.com/PrakritiGupta06/tunestack.git
cd tunestack

python3 -m venv .venv
source .venv/bin/activate
pip install -r model/requirements.txt -r api/requirements.txt

# Build model artifacts (one-time)
cd model
python download_data.py
python build_recommender.py
cd ..

# Run API
cd api
uvicorn main:app --reload