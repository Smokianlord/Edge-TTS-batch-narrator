# Edge TTS Batch Narrator v1.0.0

The first public release of **Edge TTS Batch Narrator**, a Windows desktop utility for turning chapter-based Word documents into batch-generated MP3 narration.

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
