from __future__ import annotations

import asyncio
import base64
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


from docx import Document

APP_TITLE = "Edge TTS Batch Narrator"
APP_VERSION = "1.0.0"
APP_PUBLISHER = "Mystic Story Recaps"
SITE_URL = "https://edge-tts.com/"
DEFAULT_WORKERS = 5
DEFAULT_TIMEOUT_MS = 180_000
VOICE_DISPLAY = "English - US - Andrew"
VOICE_SHORT_NAME = "en-US-AndrewMultilingualNeural"
MODE_BROWSER = "Browser UI - Microsoft Edge windows"
MODE_DIRECT = "Direct Edge TTS (recommended)"


@dataclass(frozen=True)
class Chapter:
    number: int
    heading: str
    text: str

    @property
    def filename_stem(self) -> str:
        title = self.heading.strip()
        title = re.sub(r"(?i)^chapter\s+(\d+)\b[\s.:\-\u2013\u2014]*", "", title).strip()
        prefix = f"Chapter {self.number:02d}"
        if title:
            clean = sanitize_filename(title)
            if clean and clean.lower() != str(self.number):
                return f"{prefix} - {clean}"
        return prefix


def sanitize_filename(value: str, max_len: int = 120) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value)
    value = re.sub(r"\s+", " ", value).strip().rstrip(".")
    if len(value) > max_len:
        value = value[:max_len].rstrip()
    return value or "Untitled"


CHAPTER_START_RE = re.compile(
    r"^\s*Chapter\s+(\d+)\b(?:\s*[:\-\u2013\u2014.]\s*)?(.*)$",
    re.IGNORECASE | re.DOTALL,
)


def parse_docx_chapters(path: str | os.PathLike[str]) -> list[Chapter]:
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

        if m and (headingish or m.group(2).strip()):
            flush()
            current_number = int(m.group(1))
            inline_after = m.group(2).strip()
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
        mode: str,
        emit: Callable[[dict], None],
        cancel_event: threading.Event,
    ) -> None:
        self.input_docx = input_docx
        self.output_dir = output_dir
        self.workers = workers
        self.speed_label = speed_label
        self.visible = visible
        self.skip_existing = skip_existing
        self.mode = mode
        self.emit = emit
        self.cancel_event = cancel_event
        self.log_dir = self.output_dir / "_edge_tts_logs"
        self.profile_root = self.output_dir / "_edge_tts_profiles"

    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    async def run(self) -> None:
        chapters = parse_docx_chapters(self.input_docx)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
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

        if self.mode == MODE_DIRECT:
            await self._run_direct(pending)
        else:
            await self._run_browser(pending)

    async def _run_direct(self, pending: list[Chapter]) -> None:
        try:
            import edge_tts
        except ImportError as e:
            raise RuntimeError(
                "The edge-tts Python package is not installed. Run Install_and_Run.bat again to update dependencies."
            ) from e

        self.emit({"type": "worker", "worker": 0, "status": f"Direct voice: {VOICE_SHORT_NAME}"})
        job_queue: asyncio.Queue[Chapter | None] = asyncio.Queue()
        for chapter in pending:
            job_queue.put_nowait(chapter)
        for _ in range(self.workers):
            job_queue.put_nowait(None)

        tasks = [
            asyncio.create_task(self._direct_worker(edge_tts, i + 1, job_queue))
            for i in range(self.workers)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        fatal = [r for r in results if isinstance(r, Exception) and not isinstance(r, CancelledByUser)]
        if self.cancelled():
            self.emit({"type": "cancelled"})
        elif fatal:
            raise RuntimeError("; ".join(str(e) for e in fatal))
        else:
            self.emit({"type": "done", "message": "All queued chapters finished."})

    async def _direct_worker(self, edge_tts, worker_id: int, job_queue: asyncio.Queue) -> None:
        self.emit({"type": "worker", "worker": worker_id, "status": "Direct TTS ready"})
        rate = speed_to_edge_rate(self.speed_label)
        while not self.cancelled():
            chapter = await job_queue.get()
            try:
                if chapter is None:
                    return
                target = self.output_dir / f"{chapter.filename_stem}.mp3"
                max_attempts = 3
                for attempt in range(1, max_attempts + 1):
                    if self.cancelled():
                        raise CancelledByUser()
                    try:
                        self.emit(
                            {
                                "type": "status",
                                "chapter": chapter.number,
                                "status": f"Worker {worker_id}: generating direct (attempt {attempt}/{max_attempts})",
                                "worker": worker_id,
                            }
                        )
                        if target.exists():
                            target.unlink()
                        communicate = edge_tts.Communicate(
                            chapter.text,
                            VOICE_SHORT_NAME,
                            rate=rate,
                        )
                        await communicate.save(str(target))
                        if not target.exists() or target.stat().st_size < 10_000:
                            raise RuntimeError("Generated MP3 is missing or unexpectedly small.")
                        self.emit(
                            {
                                "type": "status",
                                "chapter": chapter.number,
                                "status": "Completed",
                                "worker": worker_id,
                                "file": str(target),
                            }
                        )
                        break
                    except CancelledByUser:
                        raise
                    except Exception as e:
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
            finally:
                job_queue.task_done()

    async def _run_browser(self, pending: list[Chapter]) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise RuntimeError(
                "Playwright is not installed. Run Install_and_Run.bat again to install dependencies."
            ) from e

        self.profile_root.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as pw:
            await self._browser_preflight(pw)

            job_queue: asyncio.Queue[Chapter | None] = asyncio.Queue()
            for chapter in pending:
                job_queue.put_nowait(chapter)
            for _ in range(self.workers):
                job_queue.put_nowait(None)

            tasks = [
                asyncio.create_task(self._browser_worker(pw, i + 1, job_queue))
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

    async def _browser_preflight(self, pw) -> None:
        self.emit({"type": "worker", "worker": 0, "status": "Preflight: checking edge-tts.com voice controls"})
        profile_dir = self.profile_root / "preflight"
        profile_dir.mkdir(parents=True, exist_ok=True)
        context = None
        try:
            context = await launch_edge_context(pw, profile_dir, self.visible)
            page = context.pages[0] if context.pages else await context.new_page()
            page.set_default_timeout(30_000)
            await page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60_000)
            await self._dismiss_common_overlays(page)
            await self._select_english_andrew(page)
            self.emit({"type": "worker", "worker": 0, "status": "Preflight passed: Andrew selected"})
        except Exception as e:
            if context is not None:
                try:
                    page = context.pages[0] if context.pages else None
                    if page is not None:
                        await self._capture_preflight_failure(page, e)
                except Exception:
                    pass
            raise RuntimeError(
                "Website preflight failed before chapters were started. "
                f"{e} You can switch Generation mode to '{MODE_DIRECT}' to bypass the website UI."
            ) from e
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass

    async def _browser_worker(self, pw, worker_id: int, job_queue: asyncio.Queue) -> None:
        if self.cancelled():
            return
        await asyncio.sleep((worker_id - 1) * 0.7)
        profile_dir = self.profile_root / f"worker_{worker_id}"
        profile_dir.mkdir(parents=True, exist_ok=True)
        self.emit({"type": "worker", "worker": worker_id, "status": "Launching Microsoft Edge"})
        context = None
        try:
            context = await launch_edge_context(pw, profile_dir, self.visible)
            page = context.pages[0] if context.pages else await context.new_page()
            page.set_default_timeout(30_000)
            self.emit({"type": "worker", "worker": worker_id, "status": "Ready"})

            while not self.cancelled():
                chapter = await job_queue.get()
                try:
                    if chapter is None:
                        return
                    await self._process_browser_chapter(page, worker_id, chapter)
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

    async def _process_browser_chapter(self, page, worker_id: int, chapter: Chapter) -> None:
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
        for pattern in [r"accept", r"agree", r"got it", r"close"]:
            try:
                btn = page.get_by_role("button", name=re.compile(pattern, re.I)).first
                if await btn.count() and await btn.is_visible():
                    await btn.click(timeout=1500)
                    break
            except Exception:
                pass

    async def _select_english_andrew(self, page) -> None:
        await self._ensure_english_language(page)

        # The site exposes Male/Female as buttons at the moment. This is optional
        # because selecting Andrew by name is sufficient even if the gender filter changes.
        try:
            male = page.get_by_role("button", name=re.compile(r"^male$", re.I)).first
            if await male.count() and await male.is_visible():
                await male.click(timeout=2500)
                await asyncio.sleep(0.25)
        except Exception:
            pass

        if await self._select_andrew_native(page):
            return
        if await self._select_andrew_combobox(page):
            return
        if await self._select_andrew_input(page):
            return
        if await self._select_andrew_trigger(page):
            return

        # Last generic attempt: if Andrew is already rendered somewhere, click the
        # most specific visible match.
        if await click_best_andrew_candidate(page):
            return

        debug = await collect_control_debug(page)
        try:
            (self.log_dir / "last_voice_control_debug.txt").write_text(debug, encoding="utf-8")
        except Exception:
            pass
        raise RuntimeError(
            "Could not select Andrew. The site's Voice control was found in an unexpected format. "
            "A diagnostic file was saved as _edge_tts_logs\\last_voice_control_debug.txt."
        )

    async def _ensure_english_language(self, page) -> None:
        # Native select first.
        if await select_native_option(
            page,
            must_contain=["english"],
            prefer_contain=["english", "united states", "us"],
            exclude_contain=["andrew", "voice"],
        ):
            await asyncio.sleep(0.25)
            return

        # Most edge-tts.com sessions currently start on English. If a Language
        # label and a nearby English value are both visible, do not disturb it.
        try:
            body_text = (await page.locator("body").inner_text()).lower()
            if "language" in body_text and "english" in body_text:
                return
        except Exception:
            pass

        # Custom language control fallback.
        for locator in [
            page.get_by_role("combobox", name=re.compile(r"language", re.I)),
            page.get_by_role("button", name=re.compile(r"english|language", re.I)),
            page.get_by_label(re.compile(r"language", re.I)),
        ]:
            try:
                if await locator.count():
                    item = locator.first
                    if await item.is_visible():
                        await item.click(timeout=2500)
                        await asyncio.sleep(0.2)
                        english = page.get_by_text(re.compile(r"^english(?:\s*\(.*\))?$", re.I))
                        for i in range(min(await english.count(), 20)):
                            option = english.nth(i)
                            if await option.is_visible():
                                await option.click(timeout=2500)
                                await asyncio.sleep(0.25)
                                return
            except Exception:
                pass

    async def _select_andrew_native(self, page) -> bool:
        done = await select_native_option(
            page,
            must_contain=["andrew"],
            prefer_contain=["andrew", "english", "united states", "en-us", "male"],
            exclude_contain=[],
        )
        if done:
            await asyncio.sleep(0.2)
        return done

    async def _select_andrew_combobox(self, page) -> bool:
        combos = page.get_by_role("combobox")
        scored: list[tuple[int, object]] = []
        for i in range(await combos.count()):
            combo = combos.nth(i)
            try:
                if not await combo.is_visible():
                    continue
                attrs = await element_descriptor(combo)
                score = voice_control_score(attrs)
                scored.append((score, combo))
            except Exception:
                pass
        scored.sort(key=lambda x: x[0], reverse=True)

        for _, combo in scored:
            try:
                await combo.click(timeout=2500)
                await asyncio.sleep(0.3)

                # Some custom comboboxes allow typing directly.
                try:
                    tag = (await combo.evaluate("el => el.tagName.toLowerCase()"))
                    if tag == "input":
                        await combo.fill("Andrew")
                        await asyncio.sleep(0.4)
                except Exception:
                    pass

                if await click_best_andrew_candidate(page):
                    return True

                # Common searchable popover pattern: opening the trigger creates a
                # search textbox. Type Andrew there.
                if await self._type_andrew_into_visible_search(page):
                    if await click_best_andrew_candidate(page):
                        return True
                    try:
                        await page.keyboard.press("ArrowDown")
                        await page.keyboard.press("Enter")
                        await asyncio.sleep(0.2)
                        if await page_contains_selected_andrew(page):
                            return True
                    except Exception:
                        pass
            except Exception:
                pass
        return False

    async def _select_andrew_input(self, page) -> bool:
        inputs = page.locator("input")
        scored: list[tuple[int, object]] = []
        for i in range(await inputs.count()):
            item = inputs.nth(i)
            try:
                if not await item.is_visible():
                    continue
                typ = ((await item.get_attribute("type")) or "text").lower()
                if typ in {"checkbox", "radio", "range", "hidden", "button", "submit"}:
                    continue
                desc = await element_descriptor(item)
                score = voice_control_score(desc)
                scored.append((score, item))
            except Exception:
                pass
        scored.sort(key=lambda x: x[0], reverse=True)

        for score, item in scored:
            # Even if attributes do not say "voice", the site currently exposes
            # one main input under the Voice label, so allow the highest candidate.
            if score < 0 and scored and item is not scored[0][1]:
                continue
            try:
                await item.click(timeout=2500)
                try:
                    await item.fill("Andrew")
                except Exception:
                    await page.keyboard.press("Control+A")
                    await page.keyboard.type("Andrew")
                await asyncio.sleep(0.5)
                if await click_best_andrew_candidate(page):
                    return True
                try:
                    await page.keyboard.press("ArrowDown")
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(0.3)
                    value = ((await item.input_value()) or "").lower()
                    if "andrew" in value or await page_contains_selected_andrew(page):
                        return True
                except Exception:
                    pass
            except Exception:
                pass
        return False

    async def _select_andrew_trigger(self, page) -> bool:
        # Look for a Voice label, then interact with nearby buttons/inputs.
        voice_labels = page.get_by_text(re.compile(r"^voice$", re.I))
        for i in range(min(await voice_labels.count(), 10)):
            label = voice_labels.nth(i)
            try:
                if not await label.is_visible():
                    continue
                nearby = label.locator(
                    "xpath=following::*[self::button or self::input or @role='combobox'][1]"
                )
                if await nearby.count():
                    control = nearby.first
                    if await control.is_visible():
                        await control.click(timeout=2500)
                        await asyncio.sleep(0.3)
                        if await self._type_andrew_into_visible_search(page):
                            await asyncio.sleep(0.3)
                        if await click_best_andrew_candidate(page):
                            return True
            except Exception:
                pass
        return False

    async def _type_andrew_into_visible_search(self, page) -> bool:
        boxes = page.locator("input")
        best = None
        best_score = -999
        for i in range(await boxes.count()):
            box = boxes.nth(i)
            try:
                if not await box.is_visible():
                    continue
                typ = ((await box.get_attribute("type")) or "text").lower()
                if typ in {"checkbox", "radio", "range", "hidden", "button", "submit"}:
                    continue
                desc = (await element_descriptor(box)).lower()
                score = 0
                if "search" in desc:
                    score += 6
                if "voice" in desc:
                    score += 5
                if "select" in desc:
                    score += 2
                if score > best_score:
                    best = box
                    best_score = score
            except Exception:
                pass
        if best is None:
            return False
        try:
            await best.fill("Andrew")
            await asyncio.sleep(0.5)
            return True
        except Exception:
            return False

    async def _set_speed(self, page, speed_label: str) -> None:
        if not speed_label:
            return
        try:
            btn = page.get_by_role("button", name=re.compile(rf"^{re.escape(speed_label)}$", re.I)).first
            if await btn.count() and await btn.is_visible():
                await btn.click(timeout=2500)
        except Exception:
            pass

    async def _fill_text(self, page, text: str) -> None:
        textarea = page.locator("textarea").first
        if await textarea.count() and await textarea.is_visible():
            await textarea.fill(text)
            return

        editable = page.locator('[contenteditable="true"]').first
        if await editable.count() and await editable.is_visible():
            await editable.click()
            await editable.fill(text)
            return

        boxes = page.get_by_role("textbox")
        for i in range(await boxes.count()):
            b = boxes.nth(i)
            try:
                if await b.is_visible():
                    typ = ((await b.get_attribute("type")) or "text").lower()
                    if typ in {"search", "checkbox", "radio"}:
                        continue
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
            try:
                audio = page.locator("audio").first
                if await audio.count() and await audio.is_visible():
                    src = await audio.evaluate("el => el.currentSrc || el.src || ''")
                    if src:
                        return
            except Exception:
                pass
            try:
                links = page.locator('a[download], a[href*=".mp3"], a[href^="blob:"]')
                if await links.count() > 0:
                    for i in range(min(await links.count(), 10)):
                        if await links.nth(i).is_visible():
                            return
            except Exception:
                pass
            try:
                body_text = (await page.locator("body").inner_text()).lower()
                for phrase in [
                    "generation failed",
                    "failed to generate",
                    "too many requests",
                    "rate limit",
                    "try again later",
                ]:
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

        clickable = [
            page.get_by_role("link", name=re.compile(r"download.*(audio|mp3)|(?:audio|mp3).*download", re.I)),
            page.get_by_role("button", name=re.compile(r"download.*(audio|mp3)|(?:audio|mp3).*download", re.I)),
            page.locator('a[download][href*="mp3"]'),
            page.locator("a[download]"),
        ]

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
                        pass
            except Exception:
                pass

        audio = page.locator("audio").first
        if await audio.count():
            src = await audio.evaluate(
                "el => el.currentSrc || el.src || (el.querySelector('source') && el.querySelector('source').src) || ''"
            )
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

    async def _capture_preflight_failure(self, page, error: Exception) -> None:
        try:
            await page.screenshot(path=str(self.log_dir / "preflight_voice_failure.png"), full_page=True)
        except Exception:
            pass
        try:
            html = await page.content()
            (self.log_dir / "preflight_voice_failure.html").write_text(html, encoding="utf-8")
        except Exception:
            pass
        try:
            debug = await collect_control_debug(page)
            (self.log_dir / "preflight_voice_failure.txt").write_text(
                f"ERROR: {error}\n\n{debug}", encoding="utf-8"
            )
        except Exception:
            pass

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
            debug = await collect_control_debug(page)
            (self.log_dir / f"{stem}.txt").write_text(
                f"ERROR: {error}\n\n{debug}", encoding="utf-8"
            )
        except Exception:
            pass


async def launch_edge_context(pw, profile_dir: Path, visible: bool):
    launch_kwargs = dict(
        user_data_dir=str(profile_dir),
        channel="msedge",
        headless=not visible,
        accept_downloads=True,
        viewport={"width": 1280, "height": 900},
        args=["--no-first-run", "--disable-features=Translate"],
    )
    try:
        return await pw.chromium.launch_persistent_context(**launch_kwargs)
    except Exception:
        edge_exe = find_edge_executable()
        if not edge_exe:
            raise RuntimeError("Microsoft Edge was not found. Install Edge, then run the app again.")
        launch_kwargs.pop("channel", None)
        launch_kwargs["executable_path"] = edge_exe
        return await pw.chromium.launch_persistent_context(**launch_kwargs)


def speed_to_edge_rate(speed_label: str) -> str:
    mapping = {
        "0.5x": "-50%",
        "1x": "+0%",
        "1.25x": "+25%",
        "1.5x": "+50%",
        "2x": "+100%",
        "3x": "+200%",
    }
    return mapping.get(speed_label, "+0%")


async def select_native_option(
    page,
    must_contain: Iterable[str],
    prefer_contain: Iterable[str],
    exclude_contain: Iterable[str],
) -> bool:
    must = [x.lower() for x in must_contain]
    prefer = [x.lower() for x in prefer_contain]
    exclude = [x.lower() for x in exclude_contain]
    selects = page.locator("select")
    for si in range(await selects.count()):
        select = selects.nth(si)
        try:
            options = select.locator("option")
            best_value = None
            best_label = None
            best_score = -1
            for oi in range(await options.count()):
                opt = options.nth(oi)
                label = (await opt.inner_text()).strip()
                value = (await opt.get_attribute("value")) or ""
                combined = f"{label} {value}".lower()
                if any(x not in combined for x in must):
                    continue
                if any(x in combined for x in exclude):
                    continue
                score = sum(x in combined for x in prefer)
                if score > best_score:
                    best_value = value
                    best_label = label
                    best_score = score
            if best_label:
                if best_value:
                    await select.select_option(value=best_value)
                else:
                    await select.select_option(label=best_label)
                return True
        except Exception:
            continue
    return False


async def click_best_andrew_candidate(page) -> bool:
    candidates = page.get_by_text(re.compile(r"andrew", re.I))
    scored: list[tuple[int, object]] = []
    for i in range(min(await candidates.count(), 60)):
        c = candidates.nth(i)
        try:
            if not await c.is_visible():
                continue
            txt = ((await c.inner_text()) or "").strip().lower()
            if not txt:
                continue
            score = 0
            if "andrew" in txt:
                score += 10
            if "united states" in txt or "en-us" in txt or "english (united states)" in txt:
                score += 5
            if "male" in txt:
                score += 2
            if "multilingual" in txt:
                score += 1
            # Favor concise option rows instead of large parent containers.
            if len(txt) < 160:
                score += 2
            scored.append((score, c))
        except Exception:
            pass
    scored.sort(key=lambda x: x[0], reverse=True)
    for _, c in scored:
        try:
            await c.click(timeout=3000)
            await asyncio.sleep(0.3)
            if await page_contains_selected_andrew(page):
                return True
            # Many custom dropdowns close after a successful click even if the
            # selected value is not easy to expose through accessibility APIs.
            return True
        except Exception:
            pass
    return False


async def page_contains_selected_andrew(page) -> bool:
    # Inspect likely value-bearing controls first.
    for selector in ["select", "input", "button[role='combobox']", "[role='combobox']"]:
        loc = page.locator(selector)
        for i in range(min(await loc.count(), 50)):
            el = loc.nth(i)
            try:
                if not await el.is_visible():
                    continue
                value = ""
                try:
                    value = await el.input_value()
                except Exception:
                    pass
                txt = ""
                try:
                    txt = await el.inner_text()
                except Exception:
                    pass
                attrs = await element_descriptor(el)
                if "andrew" in f"{value} {txt} {attrs}".lower():
                    return True
            except Exception:
                pass
    return False


async def element_descriptor(locator) -> str:
    try:
        return await locator.evaluate(
            """el => {
                const attrs = ['id','name','type','placeholder','aria-label','aria-labelledby','role','value'];
                const parts = attrs.map(a => `${a}=${el.getAttribute(a) || ''}`);
                let parentText = '';
                try { parentText = (el.parentElement?.innerText || '').slice(0, 300); } catch(e) {}
                return `${el.tagName} ${parts.join(' ')} parent=${parentText}`;
            }"""
        )
    except Exception:
        return ""


def voice_control_score(desc: str) -> int:
    low = desc.lower()
    score = 0
    for token, points in [
        ("voice", 12),
        ("speaker", 7),
        ("select voice", 7),
        ("choose voice", 7),
        ("search", 2),
        ("language", -8),
        ("speed", -8),
        ("autoplay", -8),
    ]:
        if token in low:
            score += points
    return score


async def collect_control_debug(page) -> str:
    lines = []
    try:
        lines.append(f"URL: {page.url}")
    except Exception:
        pass
    try:
        body = await page.locator("body").inner_text()
        lines.append("\nVISIBLE BODY TEXT (first 8000 chars):\n" + body[:8000])
    except Exception:
        pass
    for selector in ["select", "input", "button", "[role='combobox']", "[role='option']"]:
        try:
            loc = page.locator(selector)
            lines.append(f"\nCONTROLS {selector}: count={await loc.count()}")
            for i in range(min(await loc.count(), 80)):
                item = loc.nth(i)
                try:
                    visible = await item.is_visible()
                    desc = await element_descriptor(item)
                    txt = ""
                    try:
                        txt = (await item.inner_text())[:300]
                    except Exception:
                        pass
                    lines.append(f"[{i}] visible={visible} desc={desc} text={txt!r}")
                except Exception as e:
                    lines.append(f"[{i}] inspect-error={e}")
        except Exception as e:
            lines.append(f"selector-error {selector}: {e}")
    return "\n".join(lines)


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


