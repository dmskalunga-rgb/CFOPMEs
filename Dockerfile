# =========================================================
# BASE IMAGE (STABLE + SECURITY HARDENED)
# =========================================================

FROM python:3.11-slim AS base

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Python optimizations
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# =========================================================
# SYSTEM DEPENDENCIES (MINIMAL + SECURITY)
# =========================================================

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# =========================================================
# WORKDIR
# =========================================================

WORKDIR /app


# =========================================================
# DEPENDENCY LAYER (CACHE OPTIMIZATION)
# =========================================================

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


# =========================================================
# APPLICATION LAYER
# =========================================================

COPY . .


# =========================================================
# SECURITY (NON-ROOT USER)
# =========================================================

RUN useradd -m appuser && chown -R appuser /app

USER appuser


# =========================================================
# HEALTH CHECK (PRODUCTION READY)
# =========================================================

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail http://localhost:8000/health || exit 1


# =========================================================
# METADATA
# =========================================================

LABEL maintainer="Kwanza AI Team"
LABEL version="2.0.0"
LABEL description="Kwanza AI Enterprise API"


# =========================================================
# ENTRYPOINT (FASTAPI / UVICORN)
# =========================================================

CMD ["uvicorn", "api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "4", \
     "--loop", "uvloop", \
     "--http", "httptools", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]