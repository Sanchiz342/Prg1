FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 RAG_DB_PATH=/data/rag.sqlite3
WORKDIR /app

COPY requirements.txt requirements-optional.txt ./
ARG WITH_ANTHROPIC=false
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$WITH_ANTHROPIC" = "true" ]; then pip install --no-cache-dir "anthropic>=2.4"; fi

COPY app ./app
COPY sample_docs ./sample_docs
COPY eval ./eval

RUN useradd --create-home --uid 1000 rag && mkdir -p /data && chown rag /data
USER rag
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
