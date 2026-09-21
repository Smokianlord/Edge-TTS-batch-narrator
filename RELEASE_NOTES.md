# Edge TTS batch narrator v1.0.0

The first public release of **Edge TTS batch narrator**, a Windows desktop utility for turning chapter-based Word documents into batch-generated MP3 narration.

## Highlights

- **Microsoft Andrew voice by default** — `en-US-AndrewMultilingualNeural`
- **Up to 5 chapters in parallel**
- **DOCX → MP3 batch workflow**
- **Direct Edge TTS mode** for reliable generation without website dropdown automation
- **Optional Microsoft Edge browser mode**
- **Automatic chapter detection and filenames**
- **Resume support** — completed MP3 files can be skipped on later runs
- **Retries and per-chapter status tracking**
- **Professional Windows desktop interface**

## Supported input

The application expects chapter markers such as:

```text
Chapter 1
Narration text...

Chapter 2
Narration text...
```

It also supports TTS-optimized documents where the chapter heading and narration are contained in one continuous paragraph.

## Windows download

Download `EdgeTTSBatchNarrator-v1.0.0-win64.exe` from the release assets and run it normally. No command prompt is required.

## Notes

Internet access is required for speech generation. Browser UI mode additionally requires Microsoft Edge to be installed. This project is not affiliated with Microsoft or edge-tts.com.

### Windows packaging

- Embedded the application icon into both Windows executables.
- Added Windows file metadata for product name **Edge TTS batch narrator** and version **1.0.0**.
- The application window title now exactly matches the product title.
- Improved DPI-safe layout so header/subtitle text no longer clips.
- Added a scrollable settings panel so the Recommended guidance remains accessible at higher Windows display scaling.
- Added `examples/Edge TTS batch narrator - DOCX template.docx`.
