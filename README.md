# TuneStack

Content-based music recommender, built to be wrapped, containerized, and deployed to GCP.

## Roadmap

| # | Phase | Status |
|---|-------|--------|
| 1 | Content-based recommender (genre/artist → similarity) | done |
| 2 | FastAPI wrapper (/recommend endpoint) | next |
| 3 | Docker containerization | later |
| 4 | Terraform — GCP foundation (VPC, IAM, Artifact Registry) | later |
| 5 | Terraform — GKE Autopilot cluster | later |
| 6 | GitHub Actions CI (test → build → push) | later |
| 7 | K8s manifests (Deployment, Service, HPA, probes) | later |
| 8 | GitHub Actions CD (deploy on merge) | later |
| 9 | Observability (Prometheus/Grafana) | later |
| 10 | Secrets (Secret Manager + Workload Identity) | later |

## Phase 1: the model

Data: [tidytuesday Spotify Songs](https://github.com/rfordatascience/tidytuesday/tree/master/data/2020/2020-01-21), 32,833 tracks with genre/subgenre tags.

Approach: for each track, build a text "soup" from `playlist_genre` (weight 2), `playlist_subgenre` (weight 3), and `track_artist` (weight 1), vectorize with `CountVectorizer`, and rank by cosine similarity. Subgenre carries the most weight since it's the most specific meaningful signal; artist is capped at 2 appearances per recommendation list, since two tracks by the same artist in the same subgenre produce identical vectors and tie at similarity 1.0 — weighting alone doesn't break that tie, so the artist cap is enforced directly during neighbor selection.

Instead of persisting the full 5,000×5,000 similarity matrix (~200MB dense), only the top-30 neighbors per track are saved (`neighbors.pkl`, ~2.4MB + `tracks.pkl`, ~350KB). The full matrix is computed once in memory during the build and discarded.

## Running it

```
cd model
pip install -r requirements.txt
python download_data.py
python build_recommender.py
python recommend.py
```
