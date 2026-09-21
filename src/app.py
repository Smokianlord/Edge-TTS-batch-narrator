from __future__ import annotations

import asyncio
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from backend import (
    APP_PUBLISHER,
    APP_TITLE,
    APP_VERSION,
    BatchAutomation,
    DEFAULT_WORKERS,
    MODE_BROWSER,
    MODE_DIRECT,
    VOICE_DISPLAY,
    VOICE_SHORT_NAME,
    parse_docx_chapters,
)

# -----------------------------------------------------------------------------
# Visual system
# -----------------------------------------------------------------------------
BG = "#0b1220"
SURFACE = "#111a2b"
SURFACE_2 = "#172238"
SURFACE_3 = "#1d2a42"
BORDER = "#263653"
TEXT = "#f4f7fb"
MUTED = "#9aa9bd"
ACCENT = "#4f8cff"
ACCENT_HOVER = "#6ca0ff"
SUCCESS = "#2cc98a"
WARNING = "#f0b44d"
DANGER = "#ef6b73"
INFO = "#55c2ff"


class ModernButton(tk.Button):
    def __init__(self, master, *, kind="secondary", **kwargs):
        palette = {
            "primary": (ACCENT, ACCENT_HOVER, "#ffffff"),
            "danger": ("#71343b", "#8a4049", "#ffffff"),
            "secondary": (SURFACE_3, "#283958", TEXT),
            "ghost": (SURFACE, SURFACE_2, MUTED),
        }
        bg, active_bg, fg = palette.get(kind, palette["secondary"])
        kwargs.setdefault("font", ("Segoe UI", 10, "bold"))
        kwargs.setdefault("relief", "flat")
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("padx", 16)
        kwargs.setdefault("pady", 9)
        kwargs.setdefault("cursor", "hand2")
        kwargs.setdefault("bg", bg)
        kwargs.setdefault("activebackground", active_bg)
        kwargs.setdefault("fg", fg)
        kwargs.setdefault("activeforeground", fg)
        kwargs.setdefault("disabledforeground", "#617086")
        super().__init__(master, **kwargs)


class MetricCard(tk.Frame):
    def __init__(self, master, title: str, value_var: tk.StringVar, *, accent: str = ACCENT):
        super().__init__(master, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, bd=0)
        bar = tk.Frame(self, bg=accent, width=4)
        bar.pack(side="left", fill="y")
        content = tk.Frame(self, bg=SURFACE)
        content.pack(fill="both", expand=True, padx=13, pady=10)
        tk.Label(content, text=title.upper(), bg=SURFACE, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(content, textvariable=value_var, bg=SURFACE, fg=TEXT, font=("Segoe UI Semibold", 18)).pack(anchor="w", pady=(2, 0))


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1280x860")
        self.minsize(1040, 700)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._enable_high_dpi()
        self._set_icon()

        self.msg_queue: queue.Queue[dict] = queue.Queue()
        self.cancel_event = threading.Event()
        self.bg_thread: threading.Thread | None = None
        self.chapter_items: dict[int, str] = {}
        self.chapter_states: dict[int, str] = {}
        self.completed_count = 0
        self.failed_count = 0
        self.total_count = 0
        self.running = False

        settings = self._load_settings()
        downloads = Path.home() / "Downloads"
        self.input_var = tk.StringVar(value=settings.get("input_docx", ""))
        self.output_var = tk.StringVar(value=settings.get("output_dir", str(downloads / "Edge TTS Audio")))
        self.workers_var = tk.IntVar(value=int(settings.get("workers", DEFAULT_WORKERS)))
        self.speed_var = tk.StringVar(value=settings.get("speed", "1x"))
        self.mode_var = tk.StringVar(value=settings.get("mode", MODE_DIRECT))
        self.visible_var = tk.BooleanVar(value=bool(settings.get("visible", True)))
        self.skip_existing_var = tk.BooleanVar(value=bool(settings.get("skip_existing", True)))

        self.total_metric = tk.StringVar(value="0")
        self.completed_metric = tk.StringVar(value="0")
        self.failed_metric = tk.StringVar(value="0")
        self.worker_metric = tk.StringVar(value="Idle")
        self.progress_text_var = tk.StringVar(value="Ready")
        self.subtitle_var = tk.StringVar(value="Select a Word document to begin.")
        self.status_var = tk.StringVar(value="Ready")

        self._configure_ttk()
        self._build_ui()
        self._mode_changed()
        self.after(120, self._drain_messages)

    # ------------------------------------------------------------------
    # Startup / settings
    # ------------------------------------------------------------------
    @staticmethod
    def _enable_high_dpi() -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    def _set_icon(self) -> None:
        icon = resource_path("assets/app.ico")
        if icon.exists():
            try:
                self.iconbitmap(default=str(icon))
            except Exception:
                pass

    @property
    def settings_path(self) -> Path:
        if sys.platform == "win32":
            base = Path(os.environ.get("APPDATA", Path.home()))
        else:
            base = Path.home() / ".config"
        return base / "EdgeTTSBatchNarrator" / "settings.json"

    def _load_settings(self) -> dict:
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_settings(self) -> None:
        data = {
            "input_docx": self.input_var.get(),
            "output_dir": self.output_var.get(),
            "workers": max(1, min(5, int(self.workers_var.get()))),
            "speed": self.speed_var.get(),
            "mode": self.mode_var.get(),
            "visible": bool(self.visible_var.get()),
            "skip_existing": bool(self.skip_existing_var.get()),
        }
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Theme / layout
    # ------------------------------------------------------------------
    def _configure_ttk(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("Dark.TEntry", fieldbackground=SURFACE_2, foreground=TEXT, insertcolor=TEXT,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=8)
        style.map("Dark.TEntry", bordercolor=[("focus", ACCENT)])

        style.configure("Dark.TCombobox", fieldbackground=SURFACE_2, background=SURFACE_2,
                        foreground=TEXT, arrowcolor=MUTED, bordercolor=BORDER, padding=7)
        style.map("Dark.TCombobox",
                  fieldbackground=[("readonly", SURFACE_2)],
                  foreground=[("readonly", TEXT)],
                  bordercolor=[("focus", ACCENT)])

        style.configure("Dark.TSpinbox", fieldbackground=SURFACE_2, background=SURFACE_2,
                        foreground=TEXT, arrowcolor=MUTED, bordercolor=BORDER, padding=7)

        style.configure("Dark.TCheckbutton", background=SURFACE, foreground=TEXT, font=("Segoe UI", 9))
        style.map("Dark.TCheckbutton", background=[("active", SURFACE)], foreground=[("disabled", "#66758c")])

        style.configure("Blue.Horizontal.TProgressbar", troughcolor=SURFACE_2, background=ACCENT,
                        bordercolor=SURFACE_2, lightcolor=ACCENT, darkcolor=ACCENT, thickness=10)

        style.configure("Queue.Treeview", background=SURFACE, fieldbackground=SURFACE,
                        foreground=TEXT, rowheight=32, borderwidth=0, font=("Segoe UI", 9))
        style.map("Queue.Treeview", background=[("selected", "#23416e")], foreground=[("selected", "#ffffff")])
        style.configure("Queue.Treeview.Heading", background=SURFACE_2, foreground=MUTED,
                        relief="flat", borderwidth=0, font=("Segoe UI", 9, "bold"), padding=8)
        style.map("Queue.Treeview.Heading", background=[("active", SURFACE_3)])

        style.configure("Vertical.TScrollbar", background=SURFACE_2, troughcolor=SURFACE,
                        bordercolor=SURFACE, arrowcolor=MUTED)

    def _build_ui(self) -> None:
        # Header
        header = tk.Frame(self, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        header.pack(fill="x")

        brand = tk.Frame(header, bg=SURFACE)
        brand.pack(side="left", padx=22, pady=12)
        icon_box = tk.Canvas(brand, width=42, height=42, bg=SURFACE, highlightthickness=0)
        icon_box.pack(side="left")
        icon_box.create_oval(2, 2, 40, 40, fill=ACCENT, outline="")
        icon_box.create_line(13, 23, 13, 19, 17, 14, 21, 27, 25, 11, 29, 23,
                             fill="white", width=3, smooth=True)

        title_wrap = tk.Frame(brand, bg=SURFACE)
        title_wrap.pack(side="left", padx=(12, 0))
        tk.Label(title_wrap, text=APP_TITLE, bg=SURFACE, fg=TEXT,
                 font=("Segoe UI Semibold", 18)).pack(anchor="w")
        tk.Label(title_wrap, text=f"Long-form chapter narration • Andrew (US) • v{APP_VERSION}",
                 bg=SURFACE, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))

        right_header = tk.Frame(header, bg=SURFACE)
        right_header.pack(side="right", padx=22)
        ModernButton(right_header, text="About", kind="ghost", command=self._show_about).pack(side="right", pady=12)

        # Body shell
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=18)
        body.grid_columnconfigure(0, minsize=360)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self.sidebar = tk.Frame(body, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        self.sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self.main = tk.Frame(body, bg=BG)
        self.main.grid(row=0, column=1, sticky="nsew")

        self._build_sidebar()
        self._build_main()

        footer = tk.Frame(self, bg=SURFACE, height=34, highlightbackground=BORDER, highlightthickness=1)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)
        self.status_dot = tk.Canvas(footer, width=12, height=12, bg=SURFACE, highlightthickness=0)
        self.status_dot.pack(side="left", padx=(18, 4), pady=10)
        self.status_dot.create_oval(2, 2, 10, 10, fill=SUCCESS, outline="", tags="dot")
        tk.Label(footer, textvariable=self.status_var, bg=SURFACE, fg=MUTED,
                 font=("Segoe UI", 8)).pack(side="left")
        tk.Label(footer, text=f"{APP_PUBLISHER}  •  v{APP_VERSION}", bg=SURFACE, fg="#64738b",
                 font=("Segoe UI", 8)).pack(side="right", padx=18)

    def _section_title(self, parent, title: str, subtitle: str | None = None) -> None:
        tk.Label(parent, text=title, bg=SURFACE, fg=TEXT, font=("Segoe UI Semibold", 11)).pack(anchor="w")
        if subtitle:
            tk.Label(parent, text=subtitle, bg=SURFACE, fg=MUTED, font=("Segoe UI", 8),
                     wraplength=310, justify="left").pack(anchor="w", pady=(2, 0))

    def _build_sidebar(self) -> None:
        # Scrollable settings panel: prevents clipping on Windows display scaling
        # (125%, 150%, 175%) and on smaller laptop screens.
        shell = tk.Frame(self.sidebar, bg=SURFACE)
        shell.pack(fill="both", expand=True)

        canvas = tk.Canvas(shell, bg=SURFACE, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview, style="Vertical.TScrollbar")
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=SURFACE)
        inner_window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _sync_scrollregion(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_width(event):
            canvas.itemconfigure(inner_window, width=event.width)

        inner.bind("<Configure>", _sync_scrollregion)
        canvas.bind("<Configure>", _sync_width)

        def _wheel(event):
            if event.delta:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        inner.configure(padx=18, pady=18)

        self._section_title(inner, "Project", "Choose the chapter document and where the MP3 files should be saved.")
        tk.Label(inner, text="INPUT DOCX", bg=SURFACE, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(15, 5))
        input_row = tk.Frame(inner, bg=SURFACE)
        input_row.pack(fill="x")
        self.input_entry = ttk.Entry(input_row, textvariable=self.input_var, style="Dark.TEntry")
        self.input_entry.pack(side="left", fill="x", expand=True)
        ModernButton(input_row, text="Browse", kind="secondary", command=self._browse_input, padx=11, pady=7).pack(side="left", padx=(7, 0))

        tk.Label(inner, text="OUTPUT FOLDER", bg=SURFACE, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(13, 5))
        output_row = tk.Frame(inner, bg=SURFACE)
        output_row.pack(fill="x")
        self.output_entry = ttk.Entry(output_row, textvariable=self.output_var, style="Dark.TEntry")
        self.output_entry.pack(side="left", fill="x", expand=True)
        ModernButton(output_row, text="Browse", kind="secondary", command=self._browse_output, padx=11, pady=7).pack(side="left", padx=(7, 0))

        divider = tk.Frame(inner, bg=BORDER, height=1)
        divider.pack(fill="x", pady=18)

        self._section_title(inner, "Generation", "Direct mode is the fastest and most reliable. Browser mode remains available when needed.")

        tk.Label(inner, text="MODE", bg=SURFACE, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(14, 5))
        self.mode_combo = ttk.Combobox(inner, textvariable=self.mode_var, values=[MODE_DIRECT, MODE_BROWSER],
                                       state="readonly", style="Dark.TCombobox")
        self.mode_combo.pack(fill="x")
        self.mode_combo.bind("<<ComboboxSelected>>", lambda _e: self._mode_changed())

        voice_box = tk.Frame(inner, bg=SURFACE_2, highlightbackground=BORDER, highlightthickness=1)
        voice_box.pack(fill="x", pady=(13, 0))
        tk.Label(voice_box, text="VOICE", bg=SURFACE_2, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=12, pady=(9, 0))
        tk.Label(voice_box, text=VOICE_DISPLAY, bg=SURFACE_2, fg=TEXT, font=("Segoe UI Semibold", 10)).pack(anchor="w", padx=12, pady=(2, 0))
        tk.Label(voice_box, text=VOICE_SHORT_NAME, bg=SURFACE_2, fg="#7587a1", font=("Consolas", 7)).pack(anchor="w", padx=12, pady=(2, 9))

        row = tk.Frame(inner, bg=SURFACE)
        row.pack(fill="x", pady=(13, 0))
        left = tk.Frame(row, bg=SURFACE)
        left.pack(side="left", fill="x", expand=True, padx=(0, 6))
        right = tk.Frame(row, bg=SURFACE)
        right.pack(side="left", fill="x", expand=True, padx=(6, 0))
        tk.Label(left, text="WORKERS", bg=SURFACE, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 5))
        ttk.Spinbox(left, from_=1, to=5, textvariable=self.workers_var, style="Dark.TSpinbox").pack(fill="x")
        tk.Label(right, text="SPEED", bg=SURFACE, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 5))
        ttk.Combobox(right, textvariable=self.speed_var, values=["0.5x", "1x", "1.25x", "1.5x", "2x", "3x"],
                     state="readonly", style="Dark.TCombobox").pack(fill="x")

        opts = tk.Frame(inner, bg=SURFACE)
        opts.pack(fill="x", pady=(14, 0))
        self.visible_check = ttk.Checkbutton(opts, text="Show Edge browser windows", variable=self.visible_var,
                                             style="Dark.TCheckbutton")
        self.visible_check.pack(anchor="w")
        ttk.Checkbutton(opts, text="Skip completed MP3 files", variable=self.skip_existing_var,
                        style="Dark.TCheckbutton").pack(anchor="w", pady=(6, 0))

        help_box = tk.Frame(inner, bg="#10223a", highlightbackground="#1f4f7a", highlightthickness=1)
        help_box.pack(fill="x", pady=(15, 0))
        tk.Label(help_box, text="Recommended", bg="#10223a", fg=INFO,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=11, pady=(8, 2))
        self.mode_help = tk.Label(help_box, text="Direct mode uses the Andrew voice without website controls and supports up to five parallel chapters.",
                                  bg="#10223a", fg="#b7c8dc", font=("Segoe UI", 8), wraplength=300, justify="left")
        self.mode_help.pack(fill="x", anchor="w", padx=11, pady=(0, 12))

    def _build_main(self) -> None:
        top = tk.Frame(self.main, bg=BG)
        top.pack(fill="x")
        tk.Label(top, text="Batch Queue", bg=BG, fg=TEXT, font=("Segoe UI Semibold", 18)).pack(side="left")
        tk.Label(top, textvariable=self.subtitle_var, bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="left", padx=(12, 0), pady=(6, 0))

        actions = tk.Frame(top, bg=BG)
        actions.pack(side="right")
        self.start_btn = ModernButton(actions, text="Start Generation", kind="primary", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ModernButton(actions, text="Stop", kind="danger", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        ModernButton(actions, text="Open Output", kind="secondary", command=self._open_output).pack(side="left", padx=(8, 0))

        metrics = tk.Frame(self.main, bg=BG)
        metrics.pack(fill="x", pady=(16, 12))
        for i in range(4):
            metrics.grid_columnconfigure(i, weight=1)
        MetricCard(metrics, "Chapters", self.total_metric, accent=INFO).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        MetricCard(metrics, "Completed", self.completed_metric, accent=SUCCESS).grid(row=0, column=1, sticky="ew", padx=6)
        MetricCard(metrics, "Failed", self.failed_metric, accent=DANGER).grid(row=0, column=2, sticky="ew", padx=6)
        MetricCard(metrics, "Workers", self.worker_metric, accent=ACCENT).grid(row=0, column=3, sticky="ew", padx=(6, 0))

        progress_card = tk.Frame(self.main, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        progress_card.pack(fill="x", pady=(0, 12))
        ptop = tk.Frame(progress_card, bg=SURFACE)
        ptop.pack(fill="x", padx=14, pady=(10, 6))
        tk.Label(ptop, text="Overall progress", bg=SURFACE, fg=TEXT, font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Label(ptop, textvariable=self.progress_text_var, bg=SURFACE, fg=MUTED, font=("Segoe UI", 9)).pack(side="right")
        self.progress = ttk.Progressbar(progress_card, mode="determinate", style="Blue.Horizontal.TProgressbar")
        self.progress.pack(fill="x", padx=14, pady=(0, 11))

        queue_card = tk.Frame(self.main, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        queue_card.pack(fill="both", expand=True)
        table_header = tk.Frame(queue_card, bg=SURFACE)
        table_header.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(table_header, text="CHAPTER STATUS", bg=SURFACE, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        self.table_hint = tk.Label(table_header, text="No document loaded", bg=SURFACE, fg="#65758d", font=("Segoe UI", 8))
        self.table_hint.pack(side="right")

        table_wrap = tk.Frame(queue_card, bg=SURFACE)
        table_wrap.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        columns = ("chapter", "status", "worker", "file")
        self.tree = ttk.Treeview(table_wrap, columns=columns, show="headings", style="Queue.Treeview")
        self.tree.heading("chapter", text="Chapter")
        self.tree.heading("status", text="Status")
        self.tree.heading("worker", text="Worker")
        self.tree.heading("file", text="Output")
        self.tree.column("chapter", width=85, anchor="center", stretch=False)
        self.tree.column("status", width=360, anchor="w")
        self.tree.column("worker", width=75, anchor="center", stretch=False)
        self.tree.column("file", width=330, anchor="w")
        self.tree.tag_configure("completed", foreground=SUCCESS)
        self.tree.tag_configure("failed", foreground=DANGER)
        self.tree.tag_configure("active", foreground=INFO)
        self.tree.tag_configure("skipped", foreground=WARNING)
        y = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=y.set)
        self.tree.pack(side="left", fill="both", expand=True)
        y.pack(side="right", fill="y")

    # ------------------------------------------------------------------
    # UI actions
    # ------------------------------------------------------------------
    def _mode_changed(self) -> None:
        browser = self.mode_var.get() == MODE_BROWSER
        self.visible_check.configure(state="normal" if browser else "disabled")
        if browser:
            self.mode_help.configure(text="Browser mode automates edge-tts.com in Microsoft Edge and runs a voice-selection preflight before starting the queue.")
        else:
            self.mode_help.configure(text="Direct mode uses the exact Andrew voice without website controls and supports up to five parallel chapters.")

    def _browse_input(self) -> None:
        path = filedialog.askopenfilename(title="Select narration DOCX", filetypes=[("Word document", "*.docx")])
        if path:
            self.input_var.set(path)
            self._preview_document(Path(path))

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Choose MP3 output folder")
        if path:
            self.output_var.set(path)

    def _open_output(self) -> None:
        path = Path(self.output_var.get()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not open the output folder.\n\n{exc}")

    def _preview_document(self, path: Path) -> None:
        try:
            chapters = parse_docx_chapters(path)
        except Exception as exc:
            self.subtitle_var.set("Could not read chapters")
            self.table_hint.configure(text="Document needs Chapter 1, Chapter 2, ... headings")
            messagebox.showerror(APP_TITLE, f"Could not read chapters:\n\n{exc}")
            return
        self.total_metric.set(str(len(chapters)))
        self.subtitle_var.set(f"{len(chapters)} chapters detected")
        self.table_hint.configure(text=f"{len(chapters)} chapters ready")
        self.tree.delete(*self.tree.get_children())
        self.chapter_items.clear()
        self.chapter_states.clear()
        for c in chapters:
            item = self.tree.insert("", "end", values=(f"{c.number:02d}", "Ready", "—", f"{c.filename_stem}.mp3"))
            self.chapter_items[c.number] = item
            self.chapter_states[c.number] = "ready"

    def _start(self) -> None:
        input_path = Path(self.input_var.get()).expanduser()
        output_path = Path(self.output_var.get()).expanduser()
        if not input_path.exists() or input_path.suffix.lower() != ".docx":
            messagebox.showerror(APP_TITLE, "Choose a valid .docx narration file first.")
            return
        try:
            chapters = parse_docx_chapters(input_path)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not read chapters:\n\n{exc}")
            return

        workers = max(1, min(5, int(self.workers_var.get())))
        self.workers_var.set(workers)
        self.cancel_event.clear()
        self.completed_count = 0
        self.failed_count = 0
        self.total_count = len(chapters)
        self.running = True

        self.total_metric.set(str(self.total_count))
        self.completed_metric.set("0")
        self.failed_metric.set("0")
        self.worker_metric.set(str(workers))
        self.progress["maximum"] = max(1, self.total_count)
        self.progress["value"] = 0
        self.progress_text_var.set(f"0 / {self.total_count}")
        self.subtitle_var.set(f"Generating with {workers} parallel worker{'s' if workers != 1 else ''}")
        self.table_hint.configure(text=f"{VOICE_DISPLAY} • {self.speed_var.get()}")

        self.tree.delete(*self.tree.get_children())
        self.chapter_items.clear()
        self.chapter_states.clear()
        for c in chapters:
            item = self.tree.insert("", "end", values=(f"{c.number:02d}", "Waiting", "—", f"{c.filename_stem}.mp3"))
            self.chapter_items[c.number] = item
            self.chapter_states[c.number] = "waiting"

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._set_status("Running", INFO)
        self._save_settings()

        automation = BatchAutomation(
            input_docx=input_path,
            output_dir=output_path,
            workers=workers,
            speed_label=self.speed_var.get(),
            visible=self.visible_var.get(),
            skip_existing=self.skip_existing_var.get(),
            mode=self.mode_var.get(),
            emit=self.msg_queue.put,
            cancel_event=self.cancel_event,
        )

        def runner() -> None:
            try:
                asyncio.run(automation.run())
            except Exception as exc:
                self.msg_queue.put({"type": "fatal", "error": str(exc)})

        self.bg_thread = threading.Thread(target=runner, name="tts-batch", daemon=True)
        self.bg_thread.start()

    def _stop(self) -> None:
        if not self.running:
            return
        self.cancel_event.set()
        self.stop_btn.configure(state="disabled")
        self.subtitle_var.set("Stopping after the current network operations finish…")
        self._set_status("Stopping", WARNING)

    # ------------------------------------------------------------------
    # Worker events
    # ------------------------------------------------------------------
    def _drain_messages(self) -> None:
        try:
            while True:
                self._handle_message(self.msg_queue.get_nowait())
        except queue.Empty:
            pass
        self.after(120, self._drain_messages)

    def _handle_message(self, msg: dict) -> None:
        typ = msg.get("type")
        if typ == "chapters":
            return
        if typ == "status":
            num = int(msg["chapter"])
            item = self.chapter_items.get(num)
            if not item:
                return
            status = str(msg.get("status", ""))
            worker = msg.get("worker", "—") or "—"
            file = msg.get("file")
            if file:
                file_display = Path(file).name
            else:
                existing = self.tree.item(item, "values")
                file_display = existing[3] if existing else ""

            state = "active"
            low = status.lower()
            if status == "Completed":
                state = "completed"
            elif status.startswith("Skipped"):
                state = "skipped"
            elif "failed" in low:
                state = "failed"
            elif "waiting" in low:
                state = "waiting"

            old_state = self.chapter_states.get(num)
            if state in {"completed", "skipped"} and old_state not in {"completed", "skipped"}:
                self.completed_count += 1
            if state == "failed" and old_state != "failed":
                self.failed_count += 1
            self.chapter_states[num] = state

            self.tree.item(item, values=(f"{num:02d}", status, worker, file_display), tags=(state,))
            self.tree.see(item)
            self._update_progress()
            return

        if typ == "worker":
            worker = msg.get("worker")
            label = "Preflight" if worker == 0 else f"Worker {worker}"
            self.status_var.set(f"{label}: {msg.get('status', '')}")
            return

        if typ == "done":
            self._finish_run(msg.get("message", "All queued chapters finished."), cancelled=False)
            messagebox.showinfo(APP_TITLE, "Narration generation finished.\n\nYour MP3 files are ready in the output folder.")
            return

        if typ == "cancelled":
            self._finish_run("Batch generation stopped.", cancelled=True)
            return

        if typ == "fatal":
            error = msg.get("error", "Unknown error")
            self._finish_run("Generation stopped because of an error.", cancelled=True)
            messagebox.showerror(APP_TITLE, error)

    def _update_progress(self) -> None:
        done = min(self.total_count, self.completed_count + self.failed_count)
        self.completed_metric.set(str(self.completed_count))
        self.failed_metric.set(str(self.failed_count))
        self.progress["value"] = done
        if self.total_count:
            pct = int(round((done / self.total_count) * 100))
            self.progress_text_var.set(f"{done} / {self.total_count}  •  {pct}%")
        else:
            self.progress_text_var.set("Ready")

    def _finish_run(self, message: str, *, cancelled: bool) -> None:
        self.running = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.worker_metric.set("Idle")
        self.subtitle_var.set(message)
        self._set_status("Stopped" if cancelled else "Complete", WARNING if cancelled else SUCCESS)
        self._save_settings()

    def _set_status(self, text: str, color: str) -> None:
        self.status_var.set(text)
        try:
            self.status_dot.itemconfigure("dot", fill=color)
        except Exception:
            pass

    def _show_about(self) -> None:
        messagebox.showinfo(
            f"About {APP_TITLE}",
            f"{APP_TITLE} v{APP_VERSION}\n\n"
            "Batch-generate chapter MP3 narration from DOCX files using Microsoft Edge TTS.\n\n"
            f"Default voice: {VOICE_DISPLAY}\n"
            f"Voice ID: {VOICE_SHORT_NAME}\n"
            "Parallel workers: 1–5\n\n"
            f"Publisher: {APP_PUBLISHER}",
        )

    def _on_close(self) -> None:
        if self.running:
            if not messagebox.askyesno(APP_TITLE, "Generation is still running. Stop the batch and exit?"):
                return
            self.cancel_event.set()
        self._save_settings()
        self.destroy()


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / relative


def cli_dry_run(docx_path: str) -> int:
    chapters = parse_docx_chapters(docx_path)
    print(f"Found {len(chapters)} chapters")
    for chapter in chapters:
        print(f"{chapter.number:02d}: {len(chapter.text):,} chars -> {chapter.filename_stem}.mp3")
    return 0


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--dry-run":
        return cli_dry_run(sys.argv[2])
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
