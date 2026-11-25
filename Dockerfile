# Single-service image for hosting (Render): builds the React frontend and serves it from FastAPI.
# Local development and docker-compose keep using backend/Dockerfile.

FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# Optional, origin-restricted browser key. Render passes service env vars as build args; blank = OpenStreetMap.
ARG VITE_GOOGLE_MAPS_API_KEY=""
ENV VITE_GOOGLE_MAPS_API_KEY=$VITE_GOOGLE_MAPS_API_KEY
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    EMBEDDER_BACKEND=hash \
    SEED_DEMO_ITEMS=false \
    SEED_DEMO_BRANCHES=false \
    STATIC_DIR=/app/static

WORKDIR /app
COPY backend/requirements-deploy.txt .
RUN pip install -r requirements-deploy.txt

COPY backend/app ./app
COPY data/catalog ./catalog
COPY deploy/start.sh ./start.sh
COPY --from=web /web/dist ./static

RUN useradd --create-home --uid 10001 calcipher && mkdir -p /app/data && chown calcipher /app/data
USER calcipher

EXPOSE 8000
CMD ["sh", "start.sh"]
