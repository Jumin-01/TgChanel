FROM python:3.11-slim

# Шрифт DejaVu потрібен Pillow для резервної текстової картки
# (app/media/render.py) — на випадок, коли GIPHY нічого не знайшов
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY assets ./assets

ENV PYTHONUNBUFFERED=1
VOLUME ["/app/data"]

CMD ["python", "-m", "app.main"]
