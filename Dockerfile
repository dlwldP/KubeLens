# KubeLens 단일 이미지: React UI 빌드 + FastAPI(API·UI 서빙)
# 실행 방법은 README의 "Docker로 실행" 참고. kubeconfig는 이미지에 넣지 않고 실행 시 마운트한다.

FROM node:22-slim AS ui
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HOST=0.0.0.0 \
    PORT=8000 \
    KUBELENS_DB_URL=sqlite:////data/kubelens.db \
    KUBELENS_KUBECONFIG=/kube/config \
    KUBELENS_KUBECONFIG_DIRS=/kube
RUN pip install --no-cache-dir uv==0.11.32

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/kubelens ./kubelens
RUN uv sync --frozen --no-dev
COPY --from=ui /src/frontend/dist /app/frontend/dist

# --user $(id -u)로 실행해도 DB를 쓸 수 있게 /data는 모두에게 쓰기 허용(sticky)
RUN useradd --create-home kubelens && mkdir -p /data /kube && chmod 1777 /data
USER kubelens
VOLUME ["/data"]
EXPOSE 8000
# 기본은 0.0.0.0(포트 매핑용). --network host로 실행할 때는 HOST=127.0.0.1로 로컬에만 연다 (make docker-run).
CMD ["sh", "-c", "exec /app/backend/.venv/bin/uvicorn kubelens.api.app:app --host ${HOST} --port ${PORT}"]
