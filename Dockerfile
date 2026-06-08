FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-nld \
    tesseract-ocr-eng \
    libmagic1 \
    poppler-utils \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY backend/ ./backend/
COPY main.py .

# Create necessary directories
RUN mkdir -p /app/data /app/uploads /app/backups \
    /app/uploads/inkomsten/behandelingen \
    /app/uploads/uitgaven/praktijkinrichting \
    /app/uploads/uitgaven/vaste_lasten \
    /app/uploads/uitgaven/abonnementen \
    /app/uploads/uitgaven/materiaal \
    /app/uploads/uitgaven/materieel \
    /app/uploads/uitgaven/marketing \
    /app/uploads/uitgaven/reiskosten

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "1"]
