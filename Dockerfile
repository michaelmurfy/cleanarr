FROM node:24-alpine AS frontend
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend .
RUN npm run build

FROM python:3.14-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
RUN groupadd -g 1000 cleanarr && useradd -u 1000 -g cleanarr -d /app -s /sbin/nologin cleanarr
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend /app
COPY --from=frontend /web/dist /app/static
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# Stamped from the git tag on a release build; empty falls back to app/version.py.
ARG VERSION=""
ENV CLEANARR_VERSION=$VERSION
EXPOSE 7585
VOLUME ["/data"]
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7585"]
