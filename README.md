# web-video-transcriber

Turn a web video into a text file. Give it a URL, get back what was said.

It tries the site's own captions first, because those are fast and exact. If the video has
no captions, it downloads the audio and transcribes it locally with
[Whisper](https://github.com/openai/whisper) — nothing is sent to a transcription service.

Works with any site [yt-dlp](https://github.com/yt-dlp/yt-dlp) supports, which is most of them.

## Why this exists

An enormous amount of genuinely good knowledge lives only inside video. Conference
talks, lectures, teardowns, interviews, the one person who actually explains the
thing properly — it is all there, and almost none of it is **data**. You cannot
grep it. You cannot feed it to a model. You cannot ask it a question. You can only
sit and watch it at the speed the speaker chose.

A transcript changes what the thing *is*. Once the words are text:

- **Summarize it** — a two-hour talk becomes the five paragraphs you needed
- **Query it** — search across everything you have watched, not just one video
- **Build a dataset** — dozens of talks on a topic become a corpus you can analyze
- **Feed it to an AI** — as context, as RAG source material, as input to anything
- **Re-cut it** — generate a written digest, a study guide, a summarized podcast

None of that is possible while the knowledge is trapped in an audio waveform. The
transcription is not the point; it is the step that turns watchable content into
usable data, and everything interesting happens after it.

This tool does that step, locally, without sending your material to anyone.

## Requirements

- Python 3.10 or newer
- [ffmpeg](https://ffmpeg.org/) — `brew install ffmpeg` on macOS, `apt install ffmpeg` on Debian/Ubuntu

## Install

```bash
git clone https://github.com/young9898/web-video-transcriber.git
cd web-video-transcriber
python3 -m venv venv
source venv/bin/activate
pip install .
```

That installs a `transcribe` command into the virtual environment, along with `yt-dlp` and
Whisper.

## Usage

```bash
transcribe "https://example.com/watch?v=VIDEO_ID"       # captions if they exist, else Whisper
transcribe "https://example.com/watch?v=VIDEO_ID" -w    # skip captions, force Whisper
transcribe recording.mp3 --file                         # a local audio or video file
transcribe URL --model small                            # a larger, slower, more accurate model
transcribe URL --output-dir ~/Transcripts               # where the .txt goes
```

The transcript is written to the current directory as `Video Title [video-id].txt` unless you
pass `--output-dir`. If that file already exists, the video is skipped rather than transcribed
twice.

## How it decides

1. Fetch the video's title and ID.
2. Ask the site for English captions. If it has them, convert them to plain text and stop.
3. Otherwise download the audio, run Whisper on it locally, and delete the audio afterward.

Whisper models trade speed for accuracy: `tiny`, `base` (default), `small`, `medium`, `large`,
`turbo`. `base` is fine for clear speech; step up to `small` or `medium` for accents, crosstalk,
or poor audio. Larger models are substantially slower and use considerably more memory.

## Private videos

Some sites only serve a video to a logged-in session. If you own the account, you can lend the
tool your browser session:

```bash
transcribe URL --cookies-from-browser safari
```

**Read this before you use that flag.** It reads the cookies out of the named browser and hands
them to yt-dlp, which sends them to the site. That includes your logged-in session for that
site. Only use it on your own accounts, on a machine you trust, and be aware that some sites
treat automated access with a logged-in session as a terms-of-service violation. The flag is
off unless you type it — the tool never reaches for your cookies on its own.

Supported browsers: `brave`, `chrome`, `chromium`, `edge`, `firefox`, `opera`, `safari`,
`vivaldi`.

## Limitations

- **One video at a time.** Playlists and channels are not supported.
- **English captions only.** The Whisper fallback auto-detects language, but the caption path
  asks for English.
- **Whisper accuracy varies** with audio quality, accents, background noise, and jargon. Treat
  the output as a searchable draft, not a court record.
- **Whisper is slow on CPU.** A one-hour video on the `base` model takes a while; larger models
  take considerably longer.
- **No speaker labels or timestamps** in the output — it is one continuous block of text.

## Tests

```bash
python3 -m unittest discover -s tests
```

The suite uses mocks throughout: no network calls, and no external tools are invoked.

## Contact

Issues and pull requests are welcome. For anything that does not belong in a
public issue: **github@youngnetwork.org**

## License

MIT — see [LICENSE](LICENSE).
No strings beyond the license file, and no policing. If this saves you an
afternoon, that is the whole point. Credit is appreciated and never demanded; if
you build something better on top of it, that is the best outcome available.
