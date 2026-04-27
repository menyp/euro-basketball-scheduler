FROM python:3.11-slim

WORKDIR /app

# ortools needs libgomp at runtime (OpenMP for parallel CP-SAT)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# HF Spaces convention: bind to port 7860
ENV PORT=7860
EXPOSE 7860

CMD gunicorn app:app --workers 1 --threads 4 --timeout 600 --bind 0.0.0.0:$PORT
