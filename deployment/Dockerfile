# 前端构建产物由 API 进程直接提供
FROM node:24-slim AS web
RUN npm install -g pnpm@12.4.2
WORKDIR /app/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend ./
RUN pnpm build

FROM python:3.14-slim
WORKDIR /app/backend
RUN pip install --no-cache-dir uv==0.12.7
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/src ./src
RUN uv sync --locked --no-dev --no-editable --no-cache
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY --from=web /app/frontend/dist /app/frontend/dist
ENV PATH="/app/backend/.venv/bin:$PATH" \
    SC_MATERIAL_ROOT=/data/materials \
    SC_STATIC_ROOT=/app/frontend/dist \
    SC_API_HOST=0.0.0.0
