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
# Fetch the OCR models now, before the code is copied in: this layer too is kept across changes to the
# code, so a rebuild does not depend on the model host answering. One real read is needed (the models
# are only downloaded when first used), and the download is tried again when the network drops.
RUN for attempt in 1 2 3 4 5 6; do \
      python -c "import numpy as np; from PIL import Image, ImageDraw; from rapidocr import RapidOCR; \
from rapidocr.utils.typings import ModelType; \
im = Image.new('RGB', (640, 480), 'white'); ImageDraw.Draw(im).text((200, 220), 'MH 12.5 m2', fill='black'); \
RapidOCR(params={'Global.log_level': 'error', 'Det.model_type': ModelType.TINY})(np.asarray(im))" && exit 0; \
      echo "OCR model download failed (attempt $attempt), trying again"; sleep 15; \
    done; exit 1
COPY src ./src
# The label engine's own read, with the models already in the image: it must work without network.
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
