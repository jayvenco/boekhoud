FROM python:3.12-slim

# System dependencies: Tesseract OCR (NL+EN), PDF tools, libmagic
RUN apt-get update -q && \
    apt-get install -y -q --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-nld \
        tesseract-ocr-eng \
        libmagic1 \
        poppler-utils \
        curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

# Versie-informatie voor weergave in de app (Instellingen / sidebar).
# Wordt door GitHub Actions meegegeven; lokaal (docker build zonder --build-arg
# of "uvicorn main:app" direct) vallen deze terug op duidelijke standaardwaarden.
ARG GIT_SHA=lokaal
ARG GIT_BRANCH=onbekend
ENV GIT_SHA=$GIT_SHA
ENV GIT_BRANCH=$GIT_BRANCH

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD curl -f http://localhost:5000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "1"]
