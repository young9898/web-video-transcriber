#!/usr/bin/env python3
"""Transcribe web videos: site captions when they exist, local Whisper when they don't."""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_MODEL = "base"
WHISPER_MODELS = ("tiny", "base", "small", "medium", "large", "turbo")

INFO_TIMEOUT = 120       # metadata fetch
DOWNLOAD_TIMEOUT = 1800  # caption or audio download
WHISPER_TIMEOUT = 14400  # local transcription of a feature-length video is slow, not endless

# Browsers yt-dlp can read a cookie jar from. Anything else is rejected rather
# than forwarded, so a value like "--config-location" can't reach yt-dlp's parser.
SUPPORTED_BROWSERS = (
    "brave", "chrome", "chromium", "edge", "firefox", "opera", "safari", "vivaldi",
)


class TranscriberError(RuntimeError):
    """A failure the user should see as a one-line message, not a traceback."""


def require_tool(name: str) -> str:
    """Resolve an external tool on PATH, or explain how to get it."""
    path = shutil.which(name)
    if not path:
        raise TranscriberError(
            f"{name!r} not found on PATH. See the Requirements section of the README."
        )
    return path


def run(cmd: list, timeout: int) -> subprocess.CompletedProcess:
    """Run a command with a hard timeout. No shell, argument list only."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise TranscriberError(f"{Path(cmd[0]).name} timed out after {timeout}s")


def cookie_args(browser=None) -> list:
    """Opt-in only: the user names the browser, we never infer it from the URL."""
    if not browser:
        return []
    if browser not in SUPPORTED_BROWSERS:
        raise TranscriberError(
            f"Unsupported browser {browser!r}. Choose from: {', '.join(SUPPORTED_BROWSERS)}"
        )
    return ["--cookies-from-browser", browser]


def sanitize_filename(name: str) -> str:
    """Strip path-significant and illegal characters, collapse whitespace, cap length."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.strip(".")  # a bare ".." would otherwise escape the output directory
    return name[:150] or "transcript"


def txt_path(base: Path) -> Path:
    """Append .txt. Never with_suffix() — a dot in the title would eat part of the name."""
    return base.parent / (base.name + ".txt")


def get_video_info(url: str, browser=None) -> dict:
    """Fetch video metadata as JSON."""
    cmd = [
        require_tool("yt-dlp"), "--dump-json", "--no-download",
        *cookie_args(browser), "--", url,
    ]
    result = run(cmd, INFO_TIMEOUT)
    if result.returncode != 0:
        raise TranscriberError(f"Could not read video info: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise TranscriberError(
            "Video info was not valid JSON. Single videos only — playlists are not supported."
        )


def convert_srt_to_txt(srt_path: Path, out_path: Path) -> None:
    """Flatten an SRT subtitle file into one paragraph of plain text."""
    content = srt_path.read_text(encoding="utf-8")
    lines = []
    for line in content.split("\n"):
        line = line.strip()
        if not line or line.isdigit() or "-->" in line:
            continue
        line = re.sub(r"<[^>]+>", "", line)  # styling tags
        if line:
            lines.append(line)
    out_path.write_text(re.sub(r"\s+", " ", " ".join(lines)), encoding="utf-8")


def download_captions(url: str, base: Path, browser=None) -> bool:
    """Try the site's own captions. Returns True if a transcript was written."""
    cmd = [
        require_tool("yt-dlp"),
        "--skip-download", "--write-auto-sub", "--write-sub",
        "--sub-lang", "en", "--sub-format", "vtt", "--convert-subs", "srt",
        *cookie_args(browser),
        "-o", str(base), "--", url,
    ]
    run(cmd, DOWNLOAD_TIMEOUT)

    srt = base.parent / (base.name + ".en.srt")
    if not srt.exists():
        return False
    convert_srt_to_txt(srt, txt_path(base))
    srt.unlink()
    return True


def transcribe_with_whisper(source: str, base: Path, model=DEFAULT_MODEL,
                            is_local=False, browser=None) -> bool:
    """Transcribe locally with Whisper. Source is a URL, or a file path if is_local."""
    whisper = require_tool("whisper")

    if is_local:
        audio = Path(source).resolve()
        cleanup = False
    else:
        audio = base.parent / (base.name + ".mp3")
        cleanup = True
        print("Downloading audio...")
        cmd = [
            require_tool("yt-dlp"), "-x", "--audio-format", "mp3",
            *cookie_args(browser), "-o", str(audio), "--", source,
        ]
        result = run(cmd, DOWNLOAD_TIMEOUT)
        if result.returncode != 0:
            raise TranscriberError(f"Audio download failed: {result.stderr.strip()}")

    print(f"Transcribing with Whisper ({model})...")
    cmd = [
        whisper, str(audio), "--model", model,
        "--output_format", "txt", "--output_dir", str(base.parent),
    ]
    result = run(cmd, WHISPER_TIMEOUT)

    if cleanup:
        audio.unlink(missing_ok=True)
    if result.returncode != 0:
        raise TranscriberError(f"Whisper failed: {result.stderr.strip()}")

    # Whisper names its output after the audio file; move it to our naming scheme.
    produced = base.parent / (audio.stem + ".txt")
    target = txt_path(base)
    if produced.exists() and produced != target:
        produced.replace(target)
    return target.exists()


def transcribe(url: str, output_dir: Path, model=DEFAULT_MODEL,
               force_whisper=False, browser=None) -> Path:
    """Transcribe a video URL. Captions first unless force_whisper."""
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Fetching video info...")
    info = get_video_info(url, browser)
    title = sanitize_filename(info.get("title") or "unknown")
    video_id = sanitize_filename(str(info.get("id") or "unknown"))
    base = output_dir / f"{title} [{video_id}]"
    out = txt_path(base)

    if out.exists():
        print(f"Already transcribed: {out}")
        return out

    if not force_whisper:
        print("Looking for captions...")
        if download_captions(url, base, browser):
            return out
        print("No captions found, falling back to Whisper.")

    if transcribe_with_whisper(url, base, model, browser=browser):
        return out
    raise TranscriberError("Transcription produced no output")


def transcribe_local(file_path: str, output_dir: Path, model=DEFAULT_MODEL) -> Path:
    """Transcribe a local audio or video file with Whisper."""
    source = Path(file_path).resolve()
    if not source.exists():
        raise TranscriberError(f"File not found: {source}")

    output_dir.mkdir(parents=True, exist_ok=True)
    base = output_dir / sanitize_filename(source.stem)
    out = txt_path(base)

    if out.exists():
        print(f"Already transcribed: {out}")
        return out

    print(f"Transcribing: {source.name}")
    if transcribe_with_whisper(str(source), base, model, is_local=True):
        return out
    raise TranscriberError("Transcription produced no output")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="transcribe",
        description="Transcribe a web video: site captions if available, local Whisper if not.",
    )
    parser.add_argument("source", help="Video URL, or a local file path with --file")
    parser.add_argument("--file", "-f", action="store_true",
                        help="Treat source as a local audio or video file")
    parser.add_argument("--whisper", "-w", action="store_true",
                        help="Skip the caption check and transcribe with Whisper")
    parser.add_argument("--model", "-m", default=DEFAULT_MODEL, choices=WHISPER_MODELS,
                        help=f"Whisper model to use (default: {DEFAULT_MODEL})")
    parser.add_argument("--output-dir", "-o", type=Path, default=Path.cwd(),
                        help="Where to write the transcript (default: current directory)")
    parser.add_argument("--cookies-from-browser", dest="browser", metavar="BROWSER",
                        choices=SUPPORTED_BROWSERS,
                        help="Use your logged-in session from this browser. "
                             "This reads that browser's cookies — see the README.")
    args = parser.parse_args()

    try:
        if args.file:
            out = transcribe_local(args.source, args.output_dir, args.model)
        else:
            out = transcribe(args.source, args.output_dir, args.model,
                             force_whisper=args.whisper, browser=args.browser)
    except TranscriberError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Saved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
