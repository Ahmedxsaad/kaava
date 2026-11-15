# Running and deploying Kaava

## On your machine

Python 3.11+ and Node 20+.

```bash
uv venv && uv pip install -e ".[dev]"      # or: python -m venv .venv && .venv/bin/pip install -e ".[dev]"
bash 02-dataset/download.sh preview        # 0.5 GB; "full" for the whole 10 GB set
cd web && npm install && npm run build && cd ..
./bp serve                                 # the app and its API on http://127.0.0.1:8000
```

The exact engines need no key. Optional services switch on when their key is in `.env` (copy `.env.example`):

| Key | Switches on |
| --- | --- |
| `GEMINI_API_KEY` | the vision and language model: scans, sheets, free-form questions, examiners |
| `ANTHROPIC_API_KEY` | Claude, used instead while no Gemini key is set |
| `CLOUDINARY_URL` | "Enhance" on a small scan, and hosted evidence sheets for "Share" |
| `ELEVENLABS_API_KEY` | the answer read aloud in a natural voice |
| `KAAVA_PG_URL` | the log of questions asked, in PostgreSQL (Aiven on the public demo): a login that owns a schema named `kaava` |

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `BLUEPRINTS_UI_BUDGET_EUR` | 1 (5 on the public copy) | the most the app spends on model calls per start; after that it answers from what is free |
| `BLUEPRINTS_COUNT` | `distinct` | `outlines` counts an outline the file holds twice as two, as the dataset's answers do |
| `BLUEPRINTS_WORKINGS` | 2 | how many independent workings a sheet question gets |
| `BLUEPRINTS_EXAMINERS` | 2 | how many examiners look inside a computed area; 0 turns them off |
| `KAAVA_SECOND_READER` | `gemma-4-31b-it` | the different model that reads a free-form question the second time; `same` lets the first model read it twice |
| `BLUEPRINTS_WAIT_S` | 50 | how long a request waits for a model before answering "still working" (the page asks again) |
| `BLUEPRINTS_GEMINI_RESOLUTION` | `ultra_high` | the image resolution Gemini is sent |
| `BLUEPRINTS_OFFLINE` | unset | never reach an outside service (tests, air-gapped demos) |

## Docker

```bash
docker build -t blueprints-talk .
docker run --rm -p 8000:8000 -v "$PWD/data:/app/data:ro" -v "$PWD/cache:/app/cache" --env-file .env blueprints-talk
```

The drawings are not baked into the image (each source has its own licence): mount the dataset and the cache.

## A public copy

Two ways, both behind a Cloudflare Tunnel so no port is opened:

- **This machine:** `scripts/serve_public.sh start|update|status|tunnel|stop`. A watchdog restarts the tunnel if the public address stops answering.
- **A small VM:** `scripts/deploy_vm.sh update|cache|env|status|logs`. `update` sends the last commit, builds the image there and restarts the app. Where the VM is comes from a git-ignored `.env.deploy` (`STOA_VM=user@host`, `STOA_VM_KEY=path of its ssh key`).

Two vCPUs and 8 GB are enough: the app uses about 220 MB at rest, the exact engines answer in under a millisecond, and the vision model runs elsewhere.

## The one heavy job

The 3D mesh of a large IFC model takes minutes and gigabytes to build. It is built in a process of its own, so the app keeps answering, and kept in `cache/mesh` by the file's content. Build them all once with `python scripts/warm_models.py --base http://127.0.0.1:8000` and copy the cache to wherever the app runs. A model file over 150 MB has no 3D view; its questions are still answered.

## Tests

```bash
.venv/bin/python -m pytest       # the whole suite; tests that need the dataset skip when it is absent
cd web && npx tsc --noEmit -p . && npm run build
```

## CI

`.github/workflows/ci.yml` runs on every push: the test suite, the web app's build, and an accuracy regression (`scripts/ci_regression.py`: the exact engines on the dataset's preview, 1,803 questions; a drop below 99% right, or one wrong Verified answer, fails the build). The jobs run on [CRACI](https://craci.com)'s GitHub Actions-compatible runners (`runs-on: craci`, with CRACI's `ubuntu-24.04` image), which record every dependency a build pulls in. CRACI's GitHub App must be installed on the repository.
