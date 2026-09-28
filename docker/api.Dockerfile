FROM docker:27-cli AS dockercli
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home branchlab
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
WORKDIR /app
COPY requirements.lock pyproject.toml README.md ./
RUN pip install --no-cache-dir -r requirements.lock
COPY src/ src/
RUN pip install --no-cache-dir --no-deps . && mkdir -p /data && chown 10001:10001 /data
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 BRANCHLAB_DATA_DIR=/data/runs BRANCHLAB_GITHUB_STATE_DIR=/data/github
EXPOSE 8765
CMD ["branchlab", "serve", "--host", "0.0.0.0", "--port", "8765", "--data", "/data/runs"]
