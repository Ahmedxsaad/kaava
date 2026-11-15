"""Blind trials (src/blueprints/blind.py): list the ones on this machine, or put a finished one in the repository.

A blind trial is done in the browser at /blind: a person makes the key before the system answers, the
system and the model alone answer once each, and three people check the answer against the clock
(docs/BLIND-TRIAL.md). This script only moves the record; it never makes or changes one.

    python scripts/blind_trial.py list
    python scripts/blind_trial.py export <trial id or its link> [--from https://stoa.ahmedxsaad.me]

`export` reads the trial from this machine's cache/blind/, or over the API of the server named by
--from (or by the link), and writes results/blind/<date>-<id>.json. It turns down a trial that is
not finished (a key, both answers, three checks) unless --partial is given, and always turns down
a test of the rig: results/blind/ holds what people did, nothing else.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from blueprints import blind, config  # noqa: E402

OUT = config.RESULTS_DIR / "blind"
CHECKS_WANTED = 3


def where(target: str, origin: str | None) -> tuple[str, str | None]:
    """The trial's id and the server to read it from (None: this machine), from an id or a link."""
    if "://" in target:
        url = urllib.parse.urlsplit(target)
        trial = urllib.parse.parse_qs(url.query).get("t", [""])[0] or url.path.rstrip("/").rsplit("/", 1)[-1]
        return trial, origin or f"{url.scheme}://{url.netloc}"
    return target, origin


def fetch(trial: str, origin: str | None) -> dict:
    if origin is None:
        return blind.public(blind.load(trial))
    request = urllib.request.Request(f"{origin.rstrip('/')}/api/blind/{urllib.parse.quote(trial, safe='')}",
                                     headers={"User-Agent": "kaava-blind-export", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as reply:
        return json.loads(reply.read().decode("utf-8"))


def lacking(record: dict) -> list[str]:
    """What a trial still lacks before it counts as finished."""
    run, missing = record.get("run") or {}, []
    if "kaava" not in run:
        missing.append("Kaava has not answered")
    if "baseline" not in run:
        missing.append("the model alone has not answered")
    checks = len(record.get("verifications") or [])
    if "kaava" in run and not run["kaava"]["refused"] and checks < CHECKS_WANTED:
        missing.append(f"{checks} of {CHECKS_WANTED} checks done")
    return missing


def export(target: str, origin: str | None, partial: bool) -> int:
    trial, origin = where(target, origin)
    try:
        record = fetch(trial, origin)
    except blind.Refused as exc:
        print(f"{trial}: {exc.why} on this machine (name the server with --from)", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"{trial}: could not be read from {origin}: {exc}", file=sys.stderr)
        return 1
    if (record.get("key") or {}).get("rig_test"):
        print(f"{trial}: this is a test of the rig, not a trial; it is not exported", file=sys.stderr)
        return 1
    missing = lacking(record)
    if missing and not partial:
        print(f"{trial}: not finished ({'; '.join(missing)}). Finish it, or pass --partial to export it as it is.", file=sys.stderr)
        return 1
    record["exported"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "from": origin or "this machine",
                          "complete": not missing, "lacking": missing}
    out = OUT / f"{record['created'][:10].replace('-', '')}-{record['id']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(line(record))
    print(f"written to {out.relative_to(config.REPO_ROOT)}" + ("" if not missing else f"  (partial: {'; '.join(missing)})"))
    return 0


def line(record: dict) -> str:
    key, run, s = record.get("key") or {}, record.get("run") or {}, record.get("summary") or blind.summary(record)
    said = lambda part: "-" if part is None else f"{part['answer']} ({part['status']})"  # noqa: E731
    marks = "" if s.get("missed") is None else f" | missed {s['missed']}, added {s['extra']}"
    checks = "" if not s.get("verifiers") else f" | {s['verifiers']} checks, median {s['median_seconds']} s, {s['agree_with_key']} agree"
    return (f"{record['id']}  {record.get('created', '')[:16]}  {s.get('stage', ''):<10}  key {key.get('quantity', 'hidden')} by {key.get('by', '?')}"
            f" | Kaava {said(run.get('kaava'))} | alone {said(run.get('baseline'))}{marks}{checks}  {record.get('question', '')[:60]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="the trials kept on this machine")
    exporting = commands.add_parser("export", help="write a finished trial to results/blind/")
    exporting.add_argument("trial", help="the trial's id, or its link")
    exporting.add_argument("--from", dest="origin", default=None, help="the server that holds it, e.g. https://stoa.ahmedxsaad.me")
    exporting.add_argument("--partial", action="store_true", help="export a trial that is not finished, marked as such")
    args = parser.parse_args()
    if args.command == "list":
        found = blind.trials()
        for record in found:
            print(line(record) + ("  [rig test]" if (record.get("key") or {}).get("rig_test") else ""))
        if not found:
            print(f"no trial in {blind.STORE.relative_to(config.REPO_ROOT)}")
        return 0
    return export(args.trial, args.origin, args.partial)


if __name__ == "__main__":
    raise SystemExit(main())
