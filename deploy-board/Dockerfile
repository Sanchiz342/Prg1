# DeployBoard image: React build + FastAPI backend. Build context: this directory (deploy-board/).
FROM node:22-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-trixie
# git: clone repositories; docker-cli: talk to the host daemon through the mounted socket
RUN apt-get update \
    && apt-get install -y --no-install-recommends git docker-cli ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements-postgres.txt backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements-postgres.txt
COPY backend/app ./app
COPY --from=frontend /build/dist ./frontend-dist
ENV DB_FRONTEND_DIR=/app/frontend-dist PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
