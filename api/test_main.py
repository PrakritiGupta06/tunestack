from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_recommend_known_track():
    r = client.get("/recommend", params={"track_name": "Blinding Lights"})
    assert r.status_code == 200
    results = r.json()
    assert len(results) == 5
    assert results[0]["track_artist"] == "The Weeknd"
    assert results[0]["score"] == 1.0


def test_recommend_respects_n():
    r = client.get("/recommend", params={"track_name": "Blinding Lights", "n": 2})
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_recommend_unknown_track():
    r = client.get("/recommend", params={"track_name": "Definitely Not A Real Song 12345"})
    assert r.status_code == 404


def test_recommend_rejects_out_of_range_n():
    r = client.get("/recommend", params={"track_name": "Blinding Lights", "n": 50})
    assert r.status_code == 422
