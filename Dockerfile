FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV HF_HOME=/app/.hf_cache
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; \
    SentenceTransformer('BAAI/bge-base-en-v1.5');\
    CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

COPY backend/ ./backend
WORKDIR /app/backend

ENV HF_HUB_OFFLINE=1
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
