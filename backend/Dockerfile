FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# FORWARDED_ALLOW_IPS must list the reverse proxy/load balancer IPs (or "*"
# when only the platform's proxy can reach this container). Without it,
# uvicorn ignores X-Forwarded-For and every client shares one
# request.client.host -- collapsing per-IP rate limiting into a shared bucket
# any single client can exhaust for everyone.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]