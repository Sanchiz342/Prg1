FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend .
RUN npm run build

FROM python:3.12-slim
WORKDIR /srv/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY backend/alembic.ini ./alembic.ini
COPY backend/migrations ./migrations
COPY --from=web /web/dist /srv/frontend/dist
CMD ["uvicorn", "app.main:get_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
