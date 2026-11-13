#!/usr/bin/env bash
# Download Stoa's Junction 2026 dataset. Start with the preview; fetch the full set only when the pipeline works.
# Both URLs support HTTP range requests, so `curl -C -` resumes an interrupted download.
set -euo pipefail
BASE="https://fwjydgxpttajiqpfmqtj.supabase.co/storage/v1/object/public/stoa-junction-2026"
mkdir -p data && cd data

case "${1:-preview}" in
  preview)
    curl -fL -C - -o junction-dataset-preview.zip "$BASE/junction-dataset-preview.zip"   # ~0.5 GB zip, ~730 MB unpacked, 924 files
    unzip -q -o junction-dataset-preview.zip                                              # -> junction-dataset-preview/
    ;;
  full)
    curl -fL -C - -o junction-dataset.zip "$BASE/junction-dataset.zip"                   # ~10 GB zip (zip64), ~12.3 GB unpacked, 16,902 files
    unzip -q -o junction-dataset.zip                                                      # -> junction-dataset/
    ;;
  *) echo "usage: $0 [preview|full]"; exit 1;;
esac

echo "Done. Read README.md and questions.jsonl in the unpacked folder."
