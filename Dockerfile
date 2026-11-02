# ZyroXQ platform image: the React app built into the FastAPI server, one process (API, A2A agents, MCP, scheduler).
# Render runs it with $PORT set; locally: docker build -t zyroxq . && docker run --env-file .env -p 8000:8000 zyroxq

# ---- web app ---------------------------------------------------------------------------------------------------------
FROM node:22.11-alpine AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# vite writes to ../src/zyroxq/api/static (see web/vite.config.ts)
RUN npm run build

# ---- python app ------------------------------------------------------------------------------------------------------
FROM python:3.13-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# dependencies first, so code changes don't reinstall them
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/
# the escrow ABI/bytecode is read from contracts/build at runtime (hosted and sepolia modes)
COPY contracts/ contracts/
COPY --from=web /app/src/zyroxq/api/static src/zyroxq/api/static
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 zyroxq && chown -R zyroxq /app
USER zyroxq

ENV PORT=8000 \
    FORWARDED_ALLOW_IPS="*"
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz', timeout=4)"
# Secrets come from the environment (Render dashboard / --env-file), never from the image
CMD ["sh", "-c", "exec zyroxq serve --host 0.0.0.0 --port \"$PORT\""]
