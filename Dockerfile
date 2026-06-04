# syntax=docker/dockerfile:1

# ---- stage 1: build the React/Vite frontend ----
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build            # -> /web/dist

# ---- stage 2: python runtime ----
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# Server deps first (cached layer), then the parser package (no deps of its own).
COPY server/requirements.txt server/requirements.txt
RUN pip install -r server/requirements.txt
COPY pyproject.toml ./
COPY src/ src/
RUN pip install .

# App code + assets the server reads at runtime.
COPY server/ server/
COPY examples/ examples/
COPY --from=web /web/dist web/dist

# Cloud Run provides $PORT (default 8080). exec so uvicorn gets SIGTERM directly.
ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn server.app:app --host 0.0.0.0 --port ${PORT}"]
