import sys
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from fastapi.responses import RedirectResponse

# model/ isn't installed as a package -- this makes it importable from here
# regardless of what directory the process is started from. It's a
# pragmatic shortcut, not the most idiomatic long-term fix; Docker (phase 3)
# will make it moot since the whole repo lands in one predictable place.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "model"))
from recommend import recommend  # noqa: E402

app = FastAPI(title="TuneStack Recommender API")


class Recommendation(BaseModel):
    track_name: str
    track_artist: str
    score: float

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/recommend", response_model=List[Recommendation])
def get_recommendations(
    track_name: str,
    artist: Optional[str] = None,
    n: int = Query(default=5, ge=1, le=20),
):
    results = recommend(track_name, artist=artist, n=n)
    if results is None:
        raise HTTPException(status_code=404, detail=f"Track '{track_name}' not found")
    return results