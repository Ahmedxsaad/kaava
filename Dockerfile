# The verification app and its API in one image. The drawings are not baked in
# (each source has its own licence): mount the dataset, the splits' caches and the
# run log when starting it.
#
#   docker build -t blueprints-talk .
#   docker run --rm -p 8000:8000 \
#     -v "$PWD/data:/app/data:ro" -v "$PWD/cache:/app/cache" --env-file .env blueprints-talk

FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
# The dependencies first, against an empty package: this layer is rebuilt only when pyproject.toml
# changes, not on every change to the code. Generous timeouts: the build may run on a slow network.
RUN mkdir -p src/blueprints && touch src/blueprints/__init__.py \
    && pip install --no-cache-dir --default-timeout=180 --retries 10 -e .
COPY src ./src
# Fetch the OCR models now: the label engine reads scans with them, and the app may run without network.
# One real read is needed: the detector model is only downloaded when it is first used.
RUN python -c "from pathlib import Path; from PIL import Image, ImageDraw; from blueprints.engines import ocr_index; \
im = Image.new('RGB', (640, 480), 'white'); ImageDraw.Draw(im).text((200, 220), 'MH 12.5 m2', fill='black'); \
im.save('/tmp/warm.png'); ocr_index.build_index(Path('/tmp/warm.png'))" && rm /tmp/warm.png
COPY splits ./splits
COPY results ./results
# Stoa's sample sheets and what their example questions answered: taken in at start, shown at no cost.
COPY examples ./examples
COPY --from=web /web/dist ./web/dist
ENV BLUEPRINTS_UI_BUDGET_EUR=1.0
EXPOSE 8000
CMD ["python", "-I", "-m", "blueprints.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
