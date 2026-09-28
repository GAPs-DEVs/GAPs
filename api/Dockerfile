FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
# Install API dependencies only, and use an unprivileged runtime account.
COPY requirements.txt ./
RUN pip install -r requirements.txt && useradd --uid 10001 --create-home apiuser
COPY manage.py ./
COPY config/ ./config/
USER apiuser
# Bind inside the container; Compose publishes this port only on host loopback.
# Start with one worker for the small EC2 instance.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "1", "--access-logfile", "-", "--error-logfile", "-"]
