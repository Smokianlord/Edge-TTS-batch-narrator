# Edge TTS batch narrator

<img src="assets/app.png" width="128" alt="Edge TTS batch narrator icon">

A Windows app that turns a Word document split into chapters into one MP3 per chapter, using Microsoft Edge's text-to-speech voices. Made for long narrations where doing each chapter by hand takes too long.

![Edge TTS batch narrator window](assets/screenshot.png)

## Features

- Finds `Chapter 1`, `Chapter 2`, ... in a `.docx` file automatically, including chapters written as one long paragraph.
- Makes up to 5 chapters at the same time.
- Saves them as `Chapter 01.mp3`, `Chapter 02.mp3`, and so on.
- If a run stops, start it again with the same output folder and it skips the chapters already done.
- Retries failed chapters automatically.
- Shows the status of each chapter and overall progress.
- Remembers your last settings.

Default voice: English (US) Andrew, `en-US-AndrewMultilingualNeural`.

## Download

From [Releases](../../releases):

- `EdgeTTSBatchNarrator-v1.0.0-win64.exe`: everything included, just run it.
- `EdgeTTSBatchNarrator-v1.0.0-Setup.exe`: smaller download that sets itself up on first run.

## Document format

```text
Chapter 1
The first chapter text...

Chapter 2
The second chapter text...
```

or one paragraph per chapter:

```text
Chapter 1. The whole chapter text in one paragraph...
```

A sample document is in `examples/Edge TTS batch narrator - DOCX template.docx`.

## Recommended settings

| Setting | Value |
|---|---|
| Generation mode | Direct Edge TTS |
| Voice | English - US - Andrew |
| Workers | 5 (use 3 if your internet is unstable) |
| Speed | 1x |
| Skip completed MP3s | On |

**Browser mode** is an alternative that drives the edge-tts.com website in Microsoft Edge. Direct mode is more reliable.

Your document is read on your PC; only the chapter text is sent to the online voice service to make the audio. An internet connection is required.

## Build from source

With Python 3.12+, double-click `build_windows.bat`. The exe is created in `dist\`. Use `run_dev.bat` to run it without building.

Manual:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt -r requirements-build.txt
python -m PyInstaller EdgeTTSBatchNarrator.spec --noconfirm --clean
```

Pushing a version tag (for example `v1.0.0`) builds both exes on GitHub and attaches them to a release.
