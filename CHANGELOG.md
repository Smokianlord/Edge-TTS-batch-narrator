# Changelog

All notable changes to **Edge TTS Batch Narrator** are documented here.

## [1.0.0] - 2026-09-21

### Added
- First public release.
- DOCX chapter detection using `Chapter N` headings and optimized continuous chapter paragraphs.
- Microsoft Edge TTS direct generation with **English (US) Andrew** (`en-US-AndrewMultilingualNeural`).
- 1-5 parallel chapter workers.
- Optional browser automation mode using Microsoft Edge and edge-tts.com.
- Automatic MP3 naming and output-folder management.
- Resume support that skips already completed MP3 files.
- Automatic retry handling for failed chapter generation.
- Professional dark desktop UI with progress metrics and per-chapter status.
- Persistent application settings.
- Windows executable build configuration and GitHub Actions release workflow.
