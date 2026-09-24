FROM python:3.12-slim

COPY requirements.lock /opt/branchlab/requirements.lock
RUN pip install --no-cache-dir --constraint /opt/branchlab/requirements.lock fastapi httpx
COPY src/branchlab/worker.py /opt/branchlab/worker.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
USER 65532:65532
WORKDIR /tmp
ENTRYPOINT ["python", "-I", "-B", "/opt/branchlab/worker.py"]
