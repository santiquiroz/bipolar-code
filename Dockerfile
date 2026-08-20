# bipolar-code — imagen de gateway (backend + UI compilada).
# llama-server NO va dentro: corre en el host con las GPUs; apunta el provider
# llamacpp a http://host.docker.internal:4002.
FROM node:20-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "litellm[proxy]>=1.60"
COPY backend/ .
COPY --from=frontend /app/frontend/dist /app/frontend/dist

ENV LITELLM_CONFIG_DIR=/data \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
