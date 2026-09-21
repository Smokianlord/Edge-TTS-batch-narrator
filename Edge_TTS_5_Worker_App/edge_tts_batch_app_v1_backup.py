from __future__ import annotations

import asyncio
import base64
import os
import queue
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from docx import Document

APP_TITLE = "Edge TTS - 5 Worker Chapter Generator"
SITE_URL = "https://edge-tts.com/"
DEFAULT_WORKERS = 5
DEFAULT_TIMEOUT_MS = 180_000
VOICE_DISPLAY = "English - US - Andrew"


@dataclass(frozen=True)
class Chapter:
    number: int
    heading: str
    text: str

    @property
    def filename_stem(self) -> str:
        title = self.heading.strip()
        # Normalize common chapter headings into filenames that sort correctly.
        title = re.sub(r"(?i)^chapter\s+(\d+)\b[\s.:\-–—]*", "", title).strip()
        prefix = f"Chapter {self.number:02d}"
        if title:
            clean = sanitize_filename(title)
            if clean and clean.lower() != str(self.number):
                return f"{prefix} - {clean}"
        return prefix


def sanitize_filename(value: str, max_len: int = 120) -> str:
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "", value)
    value = re.sub(r"\s+", " ", value).strip().rstrip(".")
    if len(value) > max_len:
        value = value[:max_len].rstrip()
    return value or "Untitled"


CHAPTER_START_RE = re.compile(
    r"^\s*Chapter\s+(\d+)\b(?:\s*[:\-–—.]\s*)?(.*)$",
    re.IGNORECASE | re.DOTALL,
)


def parse_docx_chapters(path: str | os.PathLike[str]) -> list[Chapter]:
    """Parse chapter text from a DOCX.

    Supports both:
      1) A heading paragraph followed by body paragraphs.
      2) TTS-optimized paragraphs such as "Chapter 1. Full chapter text...".
    """
    doc = Document(path)
    chapters: list[Chapter] = []
    current_number: int | None = None
    current_heading: str | None = None
    current_parts: list[str] = []

    def flush() -> None:
        nonlocal current_number, current_heading, current_parts
        if current_number is None:
            return
        body = " ".join(p.strip() for p in current_parts if p and p.strip())
        body = re.sub(r"\s+", " ", body).strip()
        if not body:
            raise ValueError(f"Chapter {current_number} has no narration text.")
        chapters.append(
            Chapter(
                number=current_number,
                heading=current_heading or f"Chapter {current_number}",
                text=body,
            )
        )
        current_number = None
        current_heading = None
        current_parts = []

    for para in doc.paragraphs:
        raw = para.text.replace("\u00a0", " ").strip()
        if not raw:
            continue

        m = CHAPTER_START_RE.match(raw)
        is_short_heading = len(raw) <= 160
        style_name = (para.style.name or "").lower() if para.style else ""
        headingish = "heading" in style_name or is_short_heading

        # If a paragraph starts with Chapter N and then contains a long inline body,
        # it is still a chapter start even though it is not a short heading.
        if m and (headingish or m.group(2).strip()):
            flush()
            current_number = int(m.group(1))
            inline_after = m.group(2).strip()

            # Keep the visible chapter label separate from narration. If inline_after
            # is long, it is narration; if short, it may be a chapter title.
            if inline_after and len(inline_after) <= 120 and para.style and "heading" in style_name:
                current_heading = f"Chapter {current_number} - {inline_after}"
            else:
                current_heading = f"Chapter {current_number}"
                if inline_after:
                    current_parts.append(inline_after)
            continue

        if current_number is not None:
            current_parts.append(raw)

    flush()

    if not chapters:
        raise ValueError("No chapters were found. Expected headings like 'Chapter 1'.")

    # Keep document order, but reject duplicate chapter numbers because filenames
    # and progress reporting would be ambiguous.
    nums = [c.number for c in chapters]
    dupes = sorted({n for n in nums if nums.count(n) > 1})
    if dupes:
        raise ValueError(f"Duplicate chapter number(s) found: {dupes}")

    return chapters


class CancelledByUser(Exception):
    pass


class BatchAutomation:
    def __init__(
        self,
        input_docx: Path,
        output_dir: Path,
        workers: int,
        speed_label: str,
        visible: bool,
        skip_existing: bool,
        emit: Callable[[dict], None],
        cancel_event: threading.Event,
    ) -> None:
        self.input_docx = input_docx
        self.output_dir = output_dir
        self.workers = workers
        self.speed_label = speed_label
        self.visible = visible
        self.skip_existing = skip_existing
        self.emit = emit
        self.cancel_event = cancel_event
        self.log_dir = self.output_dir / "_edge_tts_logs"
        self.profile_root = self.output_dir / "_edge_tts_profiles"

    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    async def run(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise RuntimeError(
                "Playwright is not installed. Run Install_and_Run.bat once to install dependencies."
            ) from e

        chapters = parse_docx_chapters(self.input_docx)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.profile_root.mkdir(parents=True, exist_ok=True)

        self.emit({"type": "chapters", "chapters": chapters})

        pending: list[Chapter] = []
        for chapter in chapters:
            mp3 = self.output_dir / f"{chapter.filename_stem}.mp3"
            if self.skip_existing and mp3.exists() and mp3.stat().st_size > 10_000:
                self.emit(
                    {
                        "type": "status",
                        "chapter": chapter.number,
                        "status": "Skipped (already exists)",
                        "file": str(mp3),
                    }
                )
            else:
                pending.append(chapter)

        if not pending:
            self.emit({"type": "done", "message": "All chapters already exist."})
            return

        job_queue: asyncio.Queue[Chapter | None] = asyncio.Queue()
        for chapter in pending:
            job_queue.put_nowait(chapter)
        for _ in range(self.workers):
            job_queue.put_nowait(None)

        async with async_playwright() as pw:
            tasks = [
                asyncio.create_task(self._worker(pw, i + 1, job_queue))
                for i in range(self.workers)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        fatal_errors = [r for r in results if isinstance(r, Exception) and not isinstance(r, CancelledByUser)]
        if self.cancelled():
            self.emit({"type": "cancelled"})
        elif fatal_errors:
            raise RuntimeError("; ".join(str(e) for e in fatal_errors))
        else:
            self.emit({"type": "done", "message": "All queued chapters finished."})

    async def _worker(self, pw, worker_id: int, job_queue: asyncio.Queue) -> None:
        if self.cancelled():
            return

        # Stagger browser launches slightly to reduce simultaneous startup spikes.
        await asyncio.sleep((worker_id - 1) * 0.7)
        profile_dir = self.profile_root / f"worker_{worker_id}"
        profile_dir.mkdir(parents=True, exist_ok=True)

        self.emit({"type": "worker", "worker": worker_id, "status": "Launching Microsoft Edge"})
        context = None
        try:
            launch_kwargs = dict(
                user_data_dir=str(profile_dir),
                channel="msedge",
                headless=not self.visible,
                accept_downloads=True,
                viewport={"width": 1280, "height": 900},
                args=["--no-first-run", "--disable-features=Translate"],
            )
            try:
                context = await pw.chromium.launch_persistent_context(**launch_kwargs)
            except Exception:
                # Fallback for systems where the branded channel is not auto-detected.
                edge_exe = find_edge_executable()
                if not edge_exe:
                    raise RuntimeError(
                        "Microsoft Edge was not found. Install Edge, then run the app again."
                    )
                launch_kwargs.pop("channel", None)
                launch_kwargs["executable_path"] = edge_exe
                context = await pw.chromium.launch_persistent_context(**launch_kwargs)

            pages = context.pages
            page = pages[0] if pages else await context.new_page()
            page.set_default_timeout(30_000)

            self.emit({"type": "worker", "worker": worker_id, "status": "Ready"})

            while not self.cancelled():
                chapter = await job_queue.get()
                try:
                    if chapter is None:
                        return
                    await self._process_chapter(page, worker_id, chapter)
                finally:
                    job_queue.task_done()
        except CancelledByUser:
            return
        except Exception as e:
            self.emit({"type": "worker", "worker": worker_id, "status": f"Stopped: {e}"})
            raise
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass

    async def _process_chapter(self, page, worker_id: int, chapter: Chapter) -> None:
        target = self.output_dir / f"{chapter.filename_stem}.mp3"
        self.emit(
            {
                "type": "status",
                "chapter": chapter.number,
                "status": f"Worker {worker_id}: queued",
                "worker": worker_id,
            }
        )

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            if self.cancelled():
                raise CancelledByUser()
            try:
                self.emit(
                    {
                        "type": "status",
                        "chapter": chapter.number,
                        "status": f"Worker {worker_id}: generating (attempt {attempt}/{max_attempts})",
                        "worker": worker_id,
                    }
                )
                await page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60_000)
                await self._dismiss_common_overlays(page)
                await self._select_english_andrew(page)
                await self._set_speed(page, self.speed_label)
                await self._fill_text(page, chapter.text)
                await self._click_generate(page)

                await self._wait_for_generation(page)
                await self._save_audio(page, target)

                if not target.exists() or target.stat().st_size < 10_000:
                    raise RuntimeError("Downloaded MP3 is missing or unexpectedly small.")

                self.emit(
                    {
                        "type": "status",
                        "chapter": chapter.number,
                        "status": "Completed",
                        "worker": worker_id,
                        "file": str(target),
                    }
                )
                return
            except CancelledByUser:
                raise
            except Exception as e:
                await self._capture_failure(page, worker_id, chapter, attempt, e)
                self.emit(
                    {
                        "type": "status",
                        "chapter": chapter.number,
                        "status": f"Retrying: {e}" if attempt < max_attempts else f"FAILED: {e}",
                        "worker": worker_id,
                        "error": str(e),
                    }
                )
                if attempt < max_attempts:
                    await asyncio.sleep(3 * attempt)
                    continue
                return

    async def _dismiss_common_overlays(self, page) -> None:
        # Safe, optional dismissals. None is required for normal operation.
        for pattern in [r"accept", r"agree", r"got it", r"close"]:
            try:
                btn = page.get_by_role("button", name=re.compile(pattern, re.I)).first
                if await btn.count() and await btn.is_visible():
                    await btn.click(timeout=1500)
                    break
            except Exception:
                pass

    async def _select_english_andrew(self, page) -> None:
        # First try native <select> elements. This is the most reliable path and
        # survives most CSS/layout redesigns.
        language_done = await select_native_option(
            page,
            must_contain=["english"],
            prefer_contain=["english"],
            exclude_contain=["voice"],
        )
        if language_done:
            await asyncio.sleep(0.4)

        # Male filter is optional; Andrew should be directly selectable when voices
        # are exposed in a native select.
        await click_role_or_text(page, "button", r"^male$", optional=True)
        await asyncio.sleep(0.2)

        voice_done = await select_native_option(
            page,
            must_contain=["andrew"],
            prefer_contain=["united states", "us", "english", "andrew"],
            exclude_contain=[],
        )

        if not language_done:
            # Custom language dropdown fallback.
            await click_role_or_text(page, "button", r"^english$", optional=True)
            await click_role_or_text(page, "option", r"^english$", optional=True)
            await click_role_or_text(page, "text", r"^english$", optional=True)
            await asyncio.sleep(0.4)

        if not voice_done:
            # Custom voice-picker fallback. Prefer a line containing Andrew and an
            # English/US qualifier; otherwise use the first visible Andrew option.
            candidates = page.get_by_text(re.compile(r"andrew", re.I))
            count = await candidates.count()
            best = None
            best_score = -1
            for i in range(min(count, 30)):
                c = candidates.nth(i)
                try:
                    if not await c.is_visible():
                        continue
                    txt = (await c.inner_text()).lower()
                    score = sum(k in txt for k in ["andrew", "english", "united states", "us"])
                    if score > best_score:
                        best = c
                        best_score = score
                except Exception:
                    pass
            if best is not None:
                await best.click()
                voice_done = True

        if not voice_done:
            # Some UIs require opening a generic Voice control first.
            await click_role_or_text(page, "button", r"voice", optional=True)
            await asyncio.sleep(0.3)
            candidates = page.get_by_text(re.compile(r"andrew", re.I))
            for i in range(min(await candidates.count(), 30)):
                c = candidates.nth(i)
                try:
                    if await c.is_visible():
                        await c.click()
                        voice_done = True
                        break
                except Exception:
                    pass

        if not voice_done:
            raise RuntimeError("Could not select the Andrew voice on edge-tts.com.")

    async def _set_speed(self, page, speed_label: str) -> None:
        if not speed_label:
            return
        try:
            btn = page.get_by_role("button", name=re.compile(rf"^{re.escape(speed_label)}$", re.I)).first
            if await btn.count() and await btn.is_visible():
                await btn.click(timeout=2500)
                return
        except Exception:
            pass
        # Speed is not essential; leave the site's default if the UI changes.

    async def _fill_text(self, page, text: str) -> None:
        textarea = page.locator("textarea").first
        if await textarea.count():
            await textarea.fill(text)
            return

        editable = page.locator('[contenteditable="true"]').first
        if await editable.count():
            await editable.click()
            await editable.fill(text)
            return

        # Last fallback: locate a textbox that is not a select/search control.
        boxes = page.get_by_role("textbox")
        for i in range(await boxes.count()):
            b = boxes.nth(i)
            try:
                if await b.is_visible():
                    await b.fill(text)
                    return
            except Exception:
                pass
        raise RuntimeError("Could not find the text input area.")

    async def _click_generate(self, page) -> None:
        patterns = [r"generate\s*audio", r"generate", r"create\s*audio"]
        for pattern in patterns:
            try:
                btn = page.get_by_role("button", name=re.compile(pattern, re.I)).first
                if await btn.count() and await btn.is_visible():
                    await btn.click()
                    return
            except Exception:
                pass
        raise RuntimeError("Could not find the Generate Audio button.")

    async def _wait_for_generation(self, page) -> None:
        deadline = time.monotonic() + (DEFAULT_TIMEOUT_MS / 1000)
        while time.monotonic() < deadline:
            if self.cancelled():
                raise CancelledByUser()

            # Audio element is the strongest completion signal.
            try:
                audio = page.locator("audio").first
                if await audio.count() and await audio.is_visible():
                    src = await audio.evaluate("el => el.currentSrc || el.src || ''")
                    if src:
                        return
            except Exception:
                pass

            # A downloadable anchor is also sufficient.
            try:
                links = page.locator('a[download], a[href*=".mp3"], a[href^="blob:"]')
                if await links.count() > 0:
                    for i in range(min(await links.count(), 10)):
                        if await links.nth(i).is_visible():
                            return
            except Exception:
                pass

            # Surface obvious website errors early.
            try:
                body_text = (await page.locator("body").inner_text()).lower()
                for phrase in ["generation failed", "failed to generate", "too many requests", "rate limit", "try again later"]:
                    if phrase in body_text:
                        raise RuntimeError(phrase)
            except RuntimeError:
                raise
            except Exception:
                pass

            await asyncio.sleep(1.0)

        raise RuntimeError("Timed out waiting for audio generation.")

    async def _save_audio(self, page, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()

        # 1) Prefer a real browser download because it preserves the server's MP3.
        clickable = []
        clickable.append(page.get_by_role("link", name=re.compile(r"download.*(audio|mp3)|(?:audio|mp3).*download", re.I)))
        clickable.append(page.get_by_role("button", name=re.compile(r"download.*(audio|mp3)|(?:audio|mp3).*download", re.I)))
        clickable.append(page.locator('a[download][href*="mp3"]'))
        clickable.append(page.locator('a[download]'))

        for locator in clickable:
            try:
                count = await locator.count()
                for i in range(min(count, 10)):
                    item = locator.nth(i)
                    if not await item.is_visible():
                        continue
                    try:
                        async with page.expect_download(timeout=15_000) as info:
                            await item.click()
                        download = await info.value
                        await download.save_as(str(target))
                        if target.exists() and target.stat().st_size > 0:
                            return
                    except Exception:
                        # Some browsers use an anchor without triggering Playwright's
                        # Download event. Continue to the audio-source fallback.
                        pass
            except Exception:
                pass

        # 2) Robust fallback: fetch the <audio> source inside the page. This also
        # handles blob: URLs that cannot be requested from Python directly.
        audio = page.locator("audio").first
        if await audio.count():
            src = await audio.evaluate("el => el.currentSrc || el.src || (el.querySelector('source') && el.querySelector('source').src) || ''")
            if src:
                data_url = await page.evaluate(
                    """async (src) => {
                        const response = await fetch(src);
                        if (!response.ok && !src.startsWith('blob:')) throw new Error('Audio fetch failed');
                        const blob = await response.blob();
                        return await new Promise((resolve, reject) => {
                            const reader = new FileReader();
                            reader.onload = () => resolve(reader.result);
                            reader.onerror = reject;
                            reader.readAsDataURL(blob);
                        });
                    }""",
                    src,
                )
                if isinstance(data_url, str) and "," in data_url:
                    _, payload = data_url.split(",", 1)
                    target.write_bytes(base64.b64decode(payload))
                    return

        raise RuntimeError("Audio was generated, but the MP3 could not be downloaded.")

    async def _capture_failure(self, page, worker_id: int, chapter: Chapter, attempt: int, error: Exception) -> None:
        stem = f"worker_{worker_id:02d}_chapter_{chapter.number:02d}_attempt_{attempt}"
        try:
            await page.screenshot(path=str(self.log_dir / f"{stem}.png"), full_page=True)
        except Exception:
            pass
        try:
            html = await page.content()
            (self.log_dir / f"{stem}.html").write_text(html, encoding="utf-8")
        except Exception:
            pass
        try:
            (self.log_dir / f"{stem}.txt").write_text(str(error), encoding="utf-8")
        except Exception:
            pass


async def select_native_option(
    page,
    must_contain: Iterable[str],
    prefer_contain: Iterable[str],
    exclude_contain: Iterable[str],
) -> bool:
    """Select the best matching <option> across all native <select> elements."""
    must = [x.lower() for x in must_contain]
    prefer = [x.lower() for x in prefer_contain]
    exclude = [x.lower() for x in exclude_contain]
    selects = page.locator("select")
    for si in range(await selects.count()):
        select = selects.nth(si)
        try:
            options = select.locator("option")
            best_label = None
            best_score = -1
            for oi in range(await options.count()):
                opt = options.nth(oi)
                label = (await opt.inner_text()).strip()
                low = label.lower()
                if any(x not in low for x in must):
                    continue
                if any(x in low for x in exclude):
                    continue
                score = sum(x in low for x in prefer)
                if score > best_score:
                    best_label = label
                    best_score = score
            if best_label:
                await select.select_option(label=best_label)
                return True
        except Exception:
            continue
    return False


async def click_role_or_text(page, kind: str, pattern: str, optional: bool = False) -> bool:
    regex = re.compile(pattern, re.I)
    candidates = []
    if kind == "button":
        candidates.append(page.get_by_role("button", name=regex))
    elif kind == "option":
        candidates.append(page.get_by_role("option", name=regex))
    else:
        candidates.append(page.get_by_text(regex))

    for locator in candidates:
        try:
            for i in range(min(await locator.count(), 20)):
                item = locator.nth(i)
                if await item.is_visible():
                    await item.click()
                    return True
        except Exception:
            pass
    if optional:
        return False
    raise RuntimeError(f"Could not click UI element matching {pattern!r}.")


def find_edge_executable() -> str | None:
    candidates = []
    for key in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(key)
        if base:
            candidates.append(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    for p in candidates:
        if p.exists():
            return str(p)
    return None


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x720")
        self.minsize(900, 620)

        self.msg_queue: queue.Queue[dict] = queue.Queue()
        self.cancel_event = threading.Event()
        self.bg_thread: threading.Thread | None = None
        self.chapter_items: dict[int, str] = {}
        self.completed_count = 0
        self.total_count = 0

        default_doc = Path(__file__).resolve().parent / "Awakening_of_the_Divine_Kingdom_Director_Cut_TTS_Optimized.docx"
        self.input_var = tk.StringVar(value=str(default_doc) if default_doc.exists() else "")
        self.output_var = tk.StringVar(value=str(Path.home() / "Downloads" / "Awakening Divine Kingdom Audio"))
        self.workers_var = tk.IntVar(value=DEFAULT_WORKERS)
        self.speed_var = tk.StringVar(value="1x")
        self.visible_var = tk.BooleanVar(value=True)
        self.skip_existing_var = tk.BooleanVar(value=True)
        self.summary_var = tk.StringVar(value="Select a DOCX, then click Start.")
        self.worker_status_var = tk.StringVar(value="Workers: idle")

        self._build_ui()
        self.after(150, self._drain_messages)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        title = ttk.Label(root, text="Edge TTS Batch Narration", font=("Segoe UI", 18, "bold"))
        title.pack(anchor="w")
        ttk.Label(
            root,
            text="5 parallel Microsoft Edge workers • English (US) • Andrew • automatic chapter downloads",
        ).pack(anchor="w", pady=(2, 14))

        form = ttk.Frame(root)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Input DOCX:").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(form, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(form, text="Browse...", command=self._browse_input).grid(row=0, column=2, padx=(8, 0), pady=4)

        ttk.Label(form, text="Output folder:").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(form, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(form, text="Browse...", command=self._browse_output).grid(row=1, column=2, padx=(8, 0), pady=4)

        settings = ttk.LabelFrame(root, text="Generation settings", padding=10)
        settings.pack(fill="x", pady=(12, 10))

        ttk.Label(settings, text="Voice:").grid(row=0, column=0, sticky="w")
        ttk.Label(settings, text=VOICE_DISPLAY, font=("Segoe UI", 10, "bold")).grid(row=0, column=1, sticky="w", padx=(6, 24))

        ttk.Label(settings, text="Parallel Edge workers:").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(settings, from_=1, to=5, textvariable=self.workers_var, width=5).grid(row=0, column=3, sticky="w", padx=(6, 24))

        ttk.Label(settings, text="Speed:").grid(row=0, column=4, sticky="w")
        ttk.Combobox(settings, textvariable=self.speed_var, values=["0.5x", "1x", "1.25x", "1.5x", "2x", "3x"], width=8, state="readonly").grid(row=0, column=5, sticky="w", padx=(6, 0))

        ttk.Checkbutton(settings, text="Show the 5 Edge windows", variable=self.visible_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(settings, text="Skip MP3 files already completed", variable=self.skip_existing_var).grid(row=1, column=2, columnspan=3, sticky="w", pady=(8, 0))

        controls = ttk.Frame(root)
        controls.pack(fill="x", pady=(0, 10))
        self.start_btn = ttk.Button(controls, text="Start batch generation", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(controls, text="Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Open output folder", command=self._open_output).pack(side="left", padx=(8, 0))

        self.progress = ttk.Progressbar(root, mode="determinate")
        self.progress.pack(fill="x")
        ttk.Label(root, textvariable=self.summary_var).pack(anchor="w", pady=(5, 2))
        ttk.Label(root, textvariable=self.worker_status_var).pack(anchor="w", pady=(0, 8))

        columns = ("chapter", "status", "worker", "file")
        self.tree = ttk.Treeview(root, columns=columns, show="headings", height=19)
        self.tree.heading("chapter", text="Chapter")
        self.tree.heading("status", text="Status")
        self.tree.heading("worker", text="Worker")
        self.tree.heading("file", text="Output")
        self.tree.column("chapter", width=90, anchor="center", stretch=False)
        self.tree.column("status", width=310, anchor="w")
        self.tree.column("worker", width=80, anchor="center", stretch=False)
        self.tree.column("file", width=420, anchor="w")

        yscroll = ttk.Scrollbar(root, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")

    def _browse_input(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Word documents", "*.docx")])
        if path:
            self.input_var.set(path)

    def _browse_output(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.output_var.set(path)

    def _open_output(self) -> None:
        path = Path(self.output_var.get()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except AttributeError:
            import subprocess
            subprocess.Popen(["xdg-open", str(path)])

    def _start(self) -> None:
        input_path = Path(self.input_var.get()).expanduser()
        output_path = Path(self.output_var.get()).expanduser()
        if not input_path.exists() or input_path.suffix.lower() != ".docx":
            messagebox.showerror(APP_TITLE, "Please choose a valid .docx input file.")
            return
        try:
            chapters = parse_docx_chapters(input_path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Could not read chapters:\n\n{e}")
            return

        workers = max(1, min(5, int(self.workers_var.get())))
        self.workers_var.set(workers)

        self.cancel_event.clear()
        self.completed_count = 0
        self.total_count = len(chapters)
        self.progress["maximum"] = self.total_count
        self.progress["value"] = 0
        self.tree.delete(*self.tree.get_children())
        self.chapter_items.clear()
        for c in chapters:
            item = self.tree.insert("", "end", values=(c.number, "Waiting", "", ""))
            self.chapter_items[c.number] = item

        self.summary_var.set(f"Loaded {len(chapters)} chapters. Starting {workers} Edge workers...")
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        automation = BatchAutomation(
            input_docx=input_path,
            output_dir=output_path,
            workers=workers,
            speed_label=self.speed_var.get(),
            visible=self.visible_var.get(),
            skip_existing=self.skip_existing_var.get(),
            emit=self.msg_queue.put,
            cancel_event=self.cancel_event,
        )

        def runner() -> None:
            try:
                asyncio.run(automation.run())
            except Exception as e:
                self.msg_queue.put({"type": "fatal", "error": str(e)})

        self.bg_thread = threading.Thread(target=runner, daemon=True)
        self.bg_thread.start()

    def _stop(self) -> None:
        self.cancel_event.set()
        self.summary_var.set("Stopping after current browser actions...")
        self.stop_btn.configure(state="disabled")

    def _drain_messages(self) -> None:
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                self._handle_message(msg)
        except queue.Empty:
            pass
        self.after(150, self._drain_messages)

    def _handle_message(self, msg: dict) -> None:
        typ = msg.get("type")
        if typ == "status":
            num = int(msg["chapter"])
            item = self.chapter_items.get(num)
            if item:
                old = self.tree.item(item, "values")
                status = msg.get("status", "")
                worker = msg.get("worker", old[2] if old else "")
                file = msg.get("file", old[3] if old else "")
                self.tree.item(item, values=(num, status, worker, file))
                self.tree.see(item)
                if status == "Completed" or str(status).startswith("Skipped"):
                    # Count once by tagging completed items.
                    tags = set(self.tree.item(item, "tags"))
                    if "done" not in tags:
                        tags.add("done")
                        self.tree.item(item, tags=tuple(tags))
                        self.completed_count += 1
                        self.progress["value"] = self.completed_count
                self.summary_var.set(f"Completed/skipped {self.completed_count} of {self.total_count} chapters")
        elif typ == "worker":
            self.worker_status_var.set(f"Worker {msg.get('worker')}: {msg.get('status')}")
        elif typ == "done":
            self.summary_var.set(msg.get("message", "Finished."))
            self._reset_buttons()
            messagebox.showinfo(APP_TITLE, "Batch generation finished.\n\nCheck the output folder for MP3 files.")
        elif typ == "cancelled":
            self.summary_var.set("Batch generation stopped.")
            self._reset_buttons()
        elif typ == "fatal":
            self.summary_var.set("Stopped because of an error.")
            self._reset_buttons()
            messagebox.showerror(APP_TITLE, msg.get("error", "Unknown error"))

    def _reset_buttons(self) -> None:
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.worker_status_var.set("Workers: idle")


def cli_dry_run(docx_path: str) -> int:
    chapters = parse_docx_chapters(docx_path)
    print(f"Found {len(chapters)} chapters")
    for c in chapters:
        print(f"{c.number:02d}: {len(c.text):,} chars -> {c.filename_stem}.mp3")
    return 0


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3 and sys.argv[1] == "--dry-run":
        raise SystemExit(cli_dry_run(sys.argv[2]))
    App().mainloop()
