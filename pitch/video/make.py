"""Build the pitch video: narration by ElevenLabs, a scripted recording of the app, put together with ffmpeg.

    .venv/bin/python pitch/video/make.py [--base http://127.0.0.1:8000]

The app must be running. Narration clips are kept in pitch/video/audio/ and only made again when
their text changes. Needs ELEVENLABS_API_KEY in .env, node, Chrome and ffmpeg.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
VOICE = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
MODEL = "eleven_multilingual_v2"  # the slower, fuller model: this is read once, not live


def clip(text: str, key: str) -> Path:
    name = hashlib.sha1(f"{VOICE}|{MODEL}|{text}".encode()).hexdigest()[:16]
    path = HERE / "audio" / f"{name}.mp3"
    if not path.is_file():
        body = json.dumps({"text": text, "model_id": MODEL,
                           "voice_settings": {"stability": 0.55, "similarity_boost": 0.75}}).encode()
        request = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}?output_format=mp3_44100_128", data=body,
            headers={"xi-api-key": key, "content-type": "application/json"})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(urllib.request.urlopen(request, timeout=120).read())
    return path


def seconds(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--out", default=str(ROOT / "pitch" / "kaava-pitch.mp4"))
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise SystemExit("ELEVENLABS_API_KEY is not set in .env")

    segments = json.loads((HERE / "narration.json").read_text(encoding="utf-8"))
    clips = []
    for seg in segments:
        path = clip(seg["text"], key)
        seg["duration"] = round(seconds(path), 2)
        clips.append(path)
    spoken = sum(s["duration"] for s in segments)
    print(f"narration: {len(segments)} clips, {spoken:.1f} s spoken")

    work = HERE / "work"
    work.mkdir(exist_ok=True)
    for old in work.glob("f*.jpg"):
        old.unlink()
    timings, marks_file = work / "timings.json", work / "marks.json"
    timings.write_text(json.dumps(segments), encoding="utf-8")
    subprocess.run(["node", str(HERE / "record.mjs"), args.base, str(timings), str(marks_file), str(work)], check=True)
    marks = json.loads(marks_file.read_text(encoding="utf-8"))

    # The picture list starts at its first picture; times in `marks` are from the start of the recording.
    first = marks["first"]
    lead = max(0.0, marks["marks"][0]["start"] - 0.4)  # cut the loading before the first words
    length = marks["total"] - lead
    command = ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-ss", f"{max(0.0, lead - first):.2f}",
               "-i", marks["video"]]
    for path in clips:
        command += ["-i", str(path)]
    delays = [f"[{i + 1}:a]adelay={int(1000 * (m['start'] - lead))}:all=1[a{i}]" for i, m in enumerate(marks["marks"])]
    mix = "".join(f"[a{i}]" for i in range(len(clips))) + f"amix=inputs={len(clips)}:normalize=0[voice]"
    command += ["-filter_complex", ";".join(delays + [mix]), "-map", "0:v", "-map", "[voice]",
                "-t", f"{length:.2f}", "-vf", "fps=30", "-c:v", "libx264", "-preset", "slow", "-crf", "18",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", args.out]
    subprocess.run(command, check=True)
    print(f"{args.out}: {seconds(Path(args.out)):.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
