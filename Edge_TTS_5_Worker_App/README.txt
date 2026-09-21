EDGE TTS - 5 WORKER CHAPTER GENERATOR v2
========================================

WHAT CHANGED IN v2
------------------
The original build could fail at "Could not select the Andrew voice" because
edge-tts.com uses a custom/searchable Voice control rather than a stable native
HTML select on every session.

v2 fixes this in two ways:

1. DIRECT EDGE TTS MODE (RECOMMENDED)
   - Does not automate the edge-tts.com dropdown at all.
   - Uses the Microsoft Edge TTS engine directly through the edge-tts package.
   - Exact voice: en-US-AndrewMultilingualNeural
   - Runs up to 5 chapters in parallel.
   - Saves MP3s directly to the output folder.
   - This is the default mode because it is not affected by website UI changes.

2. BROWSER UI MODE
   - Still launches up to 5 real Microsoft Edge windows, as originally requested.
   - Supports native selects, comboboxes, searchable inputs, popovers, and
     keyboard selection for Andrew.
   - Runs a PRE-FLIGHT voice-selection test before any chapters are queued.
     If Andrew cannot be selected, it stops immediately instead of failing all
     50 chapters.
   - Saves detailed control diagnostics under _edge_tts_logs.

VOICE
-----
Display name: English - US - Andrew
Microsoft short name: en-US-AndrewMultilingualNeural

FIRST-TIME UPDATE / SETUP
-------------------------
1. Extract this ZIP to a normal folder.
2. Double-click Install_and_Run.bat.
3. The installer creates/updates the private .venv and installs:
   - python-docx
   - playwright
   - edge-tts
4. The app opens automatically.

RECOMMENDED WORKFLOW
--------------------
1. Leave Generation mode on:
      Direct Edge TTS - 5 parallel workers (recommended)
2. Select the TTS-optimized DOCX.
3. Choose the output folder.
4. Leave Workers = 5 and Speed = 1x.
5. Click Start batch generation.
6. The app produces:
      Chapter 01.mp3
      Chapter 02.mp3
      ...
      Chapter 50.mp3

USING FIVE VISIBLE EDGE BROWSERS
--------------------------------
If you specifically want the website workflow:
1. Change Generation mode to:
      Browser UI - 5 Microsoft Edge windows
2. Keep "Show the 5 Edge windows" enabled.
3. Click Start.
4. v2 first launches a preflight browser and confirms Andrew can be selected.
5. Only after the preflight passes are the five workers started.

RESUME AFTER INTERRUPTION
-------------------------
Keep "Skip MP3 files already completed" enabled. Re-run with the same output
folder. Existing valid MP3 files are skipped.

IF BROWSER MODE FAILS AGAIN
---------------------------
Use Direct mode immediately; it does not depend on the website controls.

Browser diagnostics are stored in:
    <output folder>\_edge_tts_logs

Important preflight files include:
    preflight_voice_failure.png
    preflight_voice_failure.html
    preflight_voice_failure.txt
    last_voice_control_debug.txt

INPUT DOCUMENT SUPPORT
----------------------
The app supports both:

A) Normal chapter format:
   Chapter 1
   Paragraph...
   Paragraph...

B) TTS-optimized continuous format:
   Chapter 1. Entire chapter narration in one continuous paragraph...

The included optimized Director Cut is already supported and contains all 50
chapters.

NOTES
-----
- Five simultaneous requests can be heavier on your connection. If generation
  becomes unreliable, reduce Workers to 3 and resume; completed MP3s are kept.
- Direct mode still uses the Microsoft Edge TTS voice; it simply removes the
  fragile website-clicking layer.
