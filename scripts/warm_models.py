"""Build the 3D mesh of every IFC model the app offers, ahead of time.

The first visit to a model otherwise waits minutes for its mesh. This asks a running server for each
one in turn (the same request the page makes: 202 while it is being built, then the mesh), so the
cache is filled before anyone looks. A built mesh is keyed by the model file's content: a cache filled
here serves any server that shares the cache folder.

    python scripts/warm_models.py [--base http://127.0.0.1:8011]
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request


def get(url: str, timeout: float = 120.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as reply:
            return reply.status, reply.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:  # a busy server: ask again
        time.sleep(5)
        return 202, str(exc).encode()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8011")
    parser.add_argument("--give-up-after", type=float, default=1800.0, help="seconds to wait for one model")
    args = parser.parse_args()

    drawings: list[str] = []
    for source in ("ifc_models", "ifc_bench"):
        _, body = get(f"{args.base}/api/drawings?source={source}&limit=200")
        drawings += [item["id"] for item in json.loads(body).get("items", []) if item.get("has_3d")]
    print(f"{len(drawings)} drawings with a model", flush=True)
    for n, drawing in enumerate(drawings, 1):
        url = f"{args.base}/api/mesh?drawing={urllib.parse.quote(drawing)}"
        start = time.time()
        while True:
            status, body = get(url)
            if status != 202 or time.time() - start > args.give_up_after:
                break
        took = time.time() - start
        what = (f"{len(body) / 1e6:.1f} MB" if status == 200 else
                "still building, left to finish on its own" if status == 202 else
                f"http {status}: {body[:120].decode('utf-8', 'replace')}")
        print(f"{n:3}/{len(drawings)}  {took:7.1f} s  {drawing}  {what}", flush=True)


if __name__ == "__main__":
    main()
