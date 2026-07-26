# ---- stage 1: build the model (needs scikit-learn, discarded after) ----
FROM python:3.12-slim AS builder
WORKDIR /build
COPY model/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY model/download_data.py model/build_recommender.py ./
RUN python download_data.py && python build_recommender.py

# ---- stage 2: what actually ships (no scikit-learn) ----
FROM python:3.12-slim
WORKDIR /app
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY api/main.py ./api/main.py
COPY model/recommend.py ./model/recommend.py
COPY --from=builder /build/neighbors.pkl /build/tracks.pkl ./model/
EXPOSE 8000
CMD ["uvicorn", "main:app", "--app-dir", "api", "--host", "0.0.0.0", "--port", "8000"]