# job_auto — Local SRE/DevOps Job Review Assistant

`job_auto` builds a local, explainable review queue for job seekers. It is
configured for Site Reliability Engineering, DevOps, Platform Engineering, and
Cloud Operations roles in Delhi NCR and remote locations.

It **does not submit applications**, use job-board credentials, bypass CAPTCHAs,
or make eligibility decisions. It collects permitted public listings, ranks them
for the candidate to review, and preserves the original job links.

## What the model does

The default `hybrid` engine combines two local signals:

1. **Exact skill coverage** — spaCy `PhraseMatcher` recognizes configured skill
   aliases when spaCy is installed; deterministic regex matching remains the
   fallback.
2. **TF-IDF + cosine similarity** — a lightweight relevance model is fitted to
   the candidate profile and the current job batch. It compares titles,
   descriptions, categories, and listed skills without sending resume or job
   text to a hosted AI service.

The final score is an explainable blend of required/preferred skill coverage,
title alignment, location alignment, and semantic relevance. It is a priority
signal for the candidate, never an automated decision to apply.

## Setup

From this directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

The model runs with spaCy's tokenizer and phrase matcher after requirements are
installed. To add spaCy's trained English pipeline as well, run:

```bash
python -m spacy download en_core_web_sm
```

If the language package is not installed, the report tells you clearly and the
TF-IDF model plus regex fallback continue to work.

## Run the offline proof

```bash
python main.py --demo --show-profile
```

This uses only bundled sample postings. Its `example.invalid` URLs are
intentionally fake and prove the pipeline without claiming to fetch live jobs.

## Find real jobs

```bash
python main.py --live --show-profile
```

`--live` uses `live_sources` in `config.yaml`. The initial source is Remotive's
public API, queried once per run and cached for six hours. The report retains
its job URL and source attribution. Review an employer's location and work-
authorization rules on each job page before applying.

For local Delhi NCR listings, add either:

- a permitted JSON/JSONL export from a saved search;
- a public RSS/Atom careers feed; or
- documented public Greenhouse or Lever company-board APIs.

Examples are commented in `config.yaml`. The pipeline does not scrape
login-protected LinkedIn, Naukri, Indeed, or similar pages.

### Use a resume file

Set `profile.resume` in `config.yaml`, or pass a one-time path:

```bash
python main.py --live --resume /path/to/resume.pdf
```

Supported formats are UTF-8 `.txt`, `.md`, `.rst`, `.tex`, and text-based
`.pdf`. A `.tex` resume is cleaned into plain text locally before matching.
Until a local file is supplied, the factual, contact-free `profile.summary` and
configured skills are used only for local matching.

## Reports

Each non-`--no-store` run creates ignored local files under `data/` and
`output/`:

| File | Contents |
| --- | --- |
| `output/job_matches.csv` | Spreadsheet-friendly queue with real URL, full description, requirements, score components, and match gaps. |
| `output/job_matches.json` | Full normalized job records, scoring explanations, and runtime model status. |
| `data/job_auto.sqlite3` | De-duplicated review queue; a reviewer-selected status is retained on future runs. |

Use `--limit 100` to retain up to 100 scored roles in a run. This supports a
daily review target; it does not mass-submit applications.

## Useful options

```bash
python main.py --help
python main.py --live --limit 100
python main.py --jobs saved_jobs.json --resume resume.txt
python main.py --rss https://example.org/jobs.rss --resume resume.txt
python main.py --demo --json --no-store
```

## Configuration safety

Keep skills and accomplishments factual. Do not add job-board passwords,
application-session cookies, or other credentials to `config.yaml` or this
repository.
