FROM python:3.12-slim

# ffmpeg for video overlay
RUN apt-get update && apt-get install -y ffmpeg fonts-noto-cjk --no-install-recommends && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY spark_maker_v4.py .

# Persistent data volume (Railway can mount this)
ENV DATA_DIR=/data
RUN mkdir -p /data /tmp/creatives /tmp/assets/girls /tmp/assets/proofs /tmp/_preview

EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "300", "--workers", "2", "spark_maker_v4:app"]
