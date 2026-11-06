"""A small native macOS GUI for Music Cut."""

from __future__ import annotations

import logging
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter.font as tkfont

import soundfile as sf

from .config import (
    APP_NAME,
    APP_VERSION,
    DEFAULT_BIT_DEPTH,
    DEFAULT_QUALITY,
    DEFAULT_SAMPLE_RATE,
    MODEL_LABEL,
    OUTPUT_ROOT,
    QUALITY_PRESETS,
    SUPPORTED_EXTENSIONS,
)
from .errors import MusicCutError, SeparationCancelled
from .progress import ProgressUpdate
from .separator import SeparationResult, StemSeparator

logger = logging.getLogger(__name__)

UI_FONT = "PingFang SC"


def _is_dark_mode() -> bool:
    try:
        result = subprocess.run(
            ["defaults", "read", "-g", "AppleInterfaceStyle"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return "dark" in result.stdout.strip().lower()
    except Exception:
        return False


def _palette() -> dict[str, str]:
    if _is_dark_mode():
        return {
            "bg": "#1C1C1E",
            "card": "#2C2C2E",
            "card_alt": "#242426",
            "text": "#F5F5F7",
            "secondary": "#A1A1A6",
            "tertiary": "#7C7C80",
            "border": "#3A3A3C",
            "accent": "#0A84FF",
            "accent_hover": "#409CFF",
            "accent_text": "#FFFFFF",
            "selected": "#102F4D",
            "success": "#30D158",
            "danger": "#FF453A",
            "disabled": "#48484A",
            "progress_trough": "#3A3A3C",
        }
    return {
        "bg": "#F5F5F7",
        "card": "#FFFFFF",
        "card_alt": "#FAFAFC",
        "text": "#1D1D1F",
        "secondary": "#6E6E73",
        "tertiary": "#86868B",
        "border": "#D2D2D7",
        "accent": "#0071E3",
        "accent_hover": "#0077ED",
        "accent_text": "#FFFFFF",
        "selected": "#EAF4FF",
        "success": "#1B8A3A",
        "danger": "#D70015",
        "disabled": "#C7C7CC",
        "progress_trough": "#E5E5EA",
    }


def _fmt_time(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _fmt_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _rounded_rect(canvas: tk.Canvas, x1, y1, x2, y2, radius, **kwargs):
    points = [
        x1 + radius,
        y1,
        x2 - radius,
        y1,
        x2,
        y1,
        x2,
        y1 + radius,
        x2,
        y2 - radius,
        x2,
        y2,
        x2 - radius,
        y2,
        x1 + radius,
        y2,
        x1,
        y2,
        x1,
        y2 - radius,
        x1,
        y1 + radius,
        x1,
        y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class AccentButton(tk.Canvas):
    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command,
        palette: dict[str, str],
        *,
        width: int = 190,
        height: int = 48,
        font_size: int = 14,
    ) -> None:
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=parent.cget("bg"),
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self._text = text
        self._command = command
        self._palette = palette
        self._width = width
        self._height = height
        self._font = (UI_FONT, font_size, "bold")
        self._enabled = True
        self._hover = False
        self.bind("<Button-1>", self._on_click)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        p = self._palette
        if not self._enabled:
            fill = p["disabled"]
            text_color = p["secondary"]
            cursor = "arrow"
        elif self._hover:
            fill = p["accent_hover"]
            text_color = p["accent_text"]
            cursor = "hand2"
        else:
            fill = p["accent"]
            text_color = p["accent_text"]
            cursor = "hand2"
        self.configure(cursor=cursor)
        _rounded_rect(
            self,
            1,
            1,
            self._width - 1,
            self._height - 1,
            self._height / 2,
            fill=fill,
            outline="",
        )
        self.create_text(
            self._width / 2,
            self._height / 2,
            text=self._text,
            fill=text_color,
            font=self._font,
        )

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._hover = False
        self._draw()

    def set_text(self, text: str) -> None:
        self._text = text
        self._draw()

    def _on_enter(self, _event=None) -> None:
        if self._enabled:
            self._hover = True
            self._draw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._draw()

    def _on_click(self, _event=None) -> None:
        if self._enabled:
            self._hover = True
            self._draw()

    def _on_release(self, event=None) -> None:
        if not self._enabled:
            return
        inside = 0 <= event.x <= self._width and 0 <= event.y <= self._height
        if inside:
            self._command()
        self._hover = False
        self._draw()


class TextButton(tk.Canvas):
    """A small flat button that lets the card background show through.

    Native ttk buttons on Aqua draw a system-grey rectangular backing, which is
    visually noisy on the white/dark cards. These custom pills keep the layout
    calm and consistent in both appearances.
    """

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command,
        palette: dict[str, str],
        *,
        kind: str = "secondary",
        height: int = 34,
        font_size: int = 12,
        width: int | None = None,
    ) -> None:
        self._font = tkfont.Font(family=UI_FONT, size=font_size)
        if width is None:
            width = max(64, self._font.measure(text) + 30)
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=parent.cget("bg"),
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self._text = text
        self._command = command
        self._palette = palette
        self._kind = kind
        self._width = width
        self._height = height
        self._enabled = True
        self._hover = False
        self.bind("<Button-1>", self._on_click)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self._draw()

    def _colors(self) -> tuple[str, str, str]:
        p = self._palette
        if not self._enabled:
            return p["card_alt"], p["border"], p["tertiary"]
        if self._kind == "primary":
            return (
                p["accent_hover"] if self._hover else p["accent"],
                p["accent"],
                p["accent_text"],
            )
        if self._kind == "tinted":
            return p["selected"], p["selected"], p["accent"]
        return (
            p["card_alt"] if self._hover else p["card"],
            p["border"],
            p["text"],
        )

    def _draw(self) -> None:
        self.delete("all")
        fill, outline, text_color = self._colors()
        _rounded_rect(
            self,
            1,
            1,
            self._width - 1,
            self._height - 1,
            self._height / 2,
            fill=fill,
            outline=outline,
            width=1,
        )
        self.create_text(
            self._width / 2,
            self._height / 2,
            text=self._text,
            fill=text_color,
            font=self._font,
        )

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._hover = False
        self.configure(cursor="hand2" if self._enabled else "arrow")
        self._draw()

    def set_text(self, text: str) -> None:
        self._text = text
        self._draw()

    def _on_enter(self, _event=None) -> None:
        if self._enabled:
            self._hover = True
            self._draw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._draw()

    def _on_click(self, _event=None) -> None:
        if self._enabled:
            self._hover = True
            self._draw()

    def _on_release(self, event=None) -> None:
        if not self._enabled:
            return
        inside = 0 <= event.x <= self._width and 0 <= event.y <= self._height
        if inside:
            self._command()
        self._hover = False
        self._draw()


class ChoiceCard(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        hint: str,
        value: str,
        palette: dict[str, str],
        command,
    ) -> None:
        super().__init__(
            parent,
            bg=palette["card"],
            highlightthickness=2,
            highlightbackground=palette["border"],
            highlightcolor=palette["accent"],
            bd=0,
            cursor="hand2",
            height=64,
        )
        self._palette = palette
        self._value = value
        self._command = command
        self._selected = False
        self._title = tk.Label(
            self,
            text=title,
            font=(UI_FONT, 13, "bold"),
            bg=palette["card"],
            fg=palette["text"],
            anchor="w",
            cursor="hand2",
        )
        self._hint = tk.Label(
            self,
            text=hint,
            font=(UI_FONT, 10),
            bg=palette["card"],
            fg=palette["secondary"],
            anchor="w",
            cursor="hand2",
        )
        self._title.pack(fill="x", padx=14, pady=(9, 0))
        self._hint.pack(fill="x", padx=14, pady=(1, 8))
        for widget in (self, self._title, self._hint):
            widget.bind("<Button-1>", self._select)
        self.grid_propagate(False)

    @property
    def value(self) -> str:
        return self._value

    def _select(self, _event=None) -> None:
        self._command(self._value)

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        p = self._palette
        bg = p["selected"] if selected else p["card"]
        border = p["accent"] if selected else p["border"]
        self.configure(
            bg=bg,
            highlightbackground=border,
            highlightcolor=border,
        )
        self._title.configure(
            bg=bg, fg=p["accent"] if selected else p["text"]
        )
        self._hint.configure(bg=bg)


class MusicCutApp:
    def __init__(self, root: tk.Tk, initial_file: str | None = None) -> None:
        self.root = root
        self.p = _palette()
        self.selected_file: Path | None = None
        self.output_root_var = tk.StringVar(value=str(OUTPUT_ROOT))
        self.quality_var = tk.StringVar(value=DEFAULT_QUALITY)
        self.depth_var = tk.StringVar(value=DEFAULT_BIT_DEPTH)
        self.guitar_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="选择一首音乐开始")
        self.detail_var = tk.StringVar(value="全部计算在本机完成 · 音频与分轨都不会上传")
        self.progress_var = tk.DoubleVar(value=0.0)
        self.output_hint_var = tk.StringVar(value="")
        self.badge_var = tk.StringVar(value="Demucs v4 · 本地")
        self._quality_cards: dict[str, ChoiceCard] = {}
        self._busy = False
        self._cancel_event: threading.Event | None = None
        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._separator: StemSeparator | None = None
        self._job_started = 0.0
        self._result_dialog: tk.Toplevel | None = None
        self._controls: list[tk.Widget] = []
        self._text_buttons: list[TextButton] = []
        self._configure_root()
        self._build()
        self._configure_styles()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.report_callback_exception = self._on_tk_exception
        if initial_file:
            self.root.after(150, lambda: self._select_path(Path(initial_file)))
        else:
            self._update_output_hint()
        self.root.after(120, self._poll_queue)
        self.root.after(90, self._activate)

    def _activate(self) -> None:
        try:
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ styling
    def _configure_root(self) -> None:
        p = self.p
        self.root.title(f"{APP_NAME} — 本地五轨分离")
        self.root.configure(bg=p["bg"])
        self.root.geometry("840x832")
        self.root.minsize(800, 800)
        self.root.option_add("*tearOff", False)
        self._make_menu()

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("aqua")
        except tk.TclError:
            pass
        p = self.p
        style.configure("TButton", font=(UI_FONT, 12))
        style.configure("TCombobox", font=(UI_FONT, 12))
        style.configure(
            "MusicCut.Horizontal.TProgressbar",
            troughcolor=p["progress_trough"],
            background=p["accent"],
            bordercolor=p["progress_trough"],
            lightcolor=p["accent"],
            darkcolor=p["accent"],
            thickness=8,
        )

    def _make_menu(self) -> None:
        # macOS supplies the standard application menu (About / Hide / Quit).
        # Adding another menu with the same name results in a duplicate in the
        # menu bar, so only the File menu is custom here.
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="选择音频…", command=self._browse_audio)
        file_menu.add_command(label="打开输出文件夹", command=self._open_output_root)
        file_menu.add_separator()
        file_menu.add_command(label=f"关于 {APP_NAME}", command=self._about)
        menubar.add_cascade(label="文件", menu=file_menu)
        self.root.configure(menu=menubar)

    def _card(self, parent: tk.Misc) -> tk.Frame:
        p = self.p
        frame = tk.Frame(
            parent,
            bg=p["card"],
            highlightthickness=1,
            highlightbackground=p["border"],
            bd=0,
        )
        return frame

    def _section_title(self, parent: tk.Misc, text: str) -> None:
        tk.Label(
            parent,
            text=text,
            font=(UI_FONT, 11, "bold"),
            bg=self.p["card"],
            fg=self.p["secondary"],
            anchor="w",
        ).pack(fill="x")

    # -------------------------------------------------------------------- layout
    def _build(self) -> None:
        p = self.p
        outer = tk.Frame(self.root, bg=p["bg"])
        outer.pack(fill="both", expand=True, padx=30, pady=(22, 16))

        header = tk.Frame(outer, bg=p["bg"])
        header.pack(fill="x")
        mark = tk.Canvas(
            header,
            width=44,
            height=44,
            bg=p["bg"],
            highlightthickness=0,
            bd=0,
        )
        mark.pack(side="left", padx=(0, 12))
        _rounded_rect(mark, 0, 0, 44, 44, 12, fill=p["accent"], outline="")
        for index, height in enumerate((10, 22, 30, 18, 12)):
            x = 9 + index * 6
            mark.create_line(
                x,
                22 - height / 2,
                x,
                22 + height / 2,
                fill=p["accent_text"],
                width=3,
                capstyle="round",
            )
        title_box = tk.Frame(header, bg=p["bg"])
        title_box.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_box,
            text=APP_NAME,
            font=(UI_FONT, 25, "bold"),
            bg=p["bg"],
            fg=p["text"],
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="本地 AI 五轨分离，为视频剪辑准备干净的分轨",
            font=(UI_FONT, 12),
            bg=p["bg"],
            fg=p["secondary"],
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))
        badge = tk.Label(
            header,
            textvariable=self.badge_var,
            font=(UI_FONT, 10),
            bg=p["card_alt"],
            fg=p["secondary"],
            padx=12,
            pady=6,
        )
        badge.pack(side="right", anchor="n")

        # ---- file card
        file_card = self._card(outer)
        file_card.pack(fill="x", pady=(20, 10))
        file_inner = tk.Frame(file_card, bg=p["card"])
        file_inner.pack(fill="x", padx=18, pady=14)
        file_top = tk.Frame(file_inner, bg=p["card"])
        file_top.pack(fill="x")
        self._section_title(file_top, "1  选择音乐")
        choose_btn = TextButton(
            file_top,
            "选择文件…",
            self._browse_audio,
            p,
            kind="tinted",
            height=34,
        )
        choose_btn.pack(side="right")
        self._text_buttons.append(choose_btn)
        self.file_name_label = tk.Label(
            file_inner,
            text="还没有选择文件",
            font=(UI_FONT, 15, "bold"),
            bg=p["card"],
            fg=p["text"],
            anchor="w",
        )
        self.file_name_label.pack(fill="x", pady=(9, 1))
        self.file_meta_label = tk.Label(
            file_inner,
            text="支持 MP3 / WAV · 文件只在本机处理",
            font=(UI_FONT, 11),
            bg=p["card"],
            fg=p["secondary"],
            anchor="w",
        )
        self.file_meta_label.pack(fill="x")
        for widget in (
            file_card,
            file_inner,
            self.file_name_label,
            self.file_meta_label,
        ):
            widget.configure(cursor="hand2")
            widget.bind("<Button-1>", lambda _event: self._browse_audio())

        # ---- output card
        out_card = self._card(outer)
        out_card.pack(fill="x", pady=10)
        out_inner = tk.Frame(out_card, bg=p["card"])
        out_inner.pack(fill="x", padx=18, pady=12)
        self._section_title(out_inner, "2  保存位置")
        row = tk.Frame(out_inner, bg=p["card"])
        row.pack(fill="x", pady=(7, 0))
        self.output_entry = ttk.Entry(
            row, textvariable=self.output_root_var, font=(UI_FONT, 12)
        )
        self.output_entry.pack(side="left", fill="x", expand=True, ipady=2)
        out_btn = TextButton(
            row,
            "更改…",
            self._browse_output,
            p,
            height=34,
            width=82,
        )
        out_btn.pack(side="left", padx=(8, 0))
        self._text_buttons.append(out_btn)
        self._controls.append(self.output_entry)
        tk.Label(
            out_inner,
            textvariable=self.output_hint_var,
            font=(UI_FONT, 10),
            bg=p["card"],
            fg=p["tertiary"],
            anchor="w",
        ).pack(fill="x", pady=(5, 0))
        self.output_root_var.trace_add("write", lambda *_: self._update_output_hint())

        # ---- quality + format card
        options_card = self._card(outer)
        options_card.pack(fill="x", pady=10)
        opt_inner = tk.Frame(options_card, bg=p["card"])
        opt_inner.pack(fill="x", padx=18, pady=13)
        self._section_title(opt_inner, "3  分离质量")
        cards_row = tk.Frame(opt_inner, bg=p["card"])
        cards_row.pack(fill="x", pady=(8, 12))
        for index, preset in enumerate(QUALITY_PRESETS.values()):
            cards_row.columnconfigure(index, weight=1, uniform="quality")
            card = ChoiceCard(
                cards_row,
                title=preset.label + (" · 推荐" if preset.key == "high" else ""),
                hint=preset.hint,
                value=preset.key,
                palette=p,
                command=self._choose_quality,
            )
            card.grid(
                row=0,
                column=index,
                sticky="nsew",
                padx=(0 if index == 0 else 6, 0 if index == 2 else 6),
            )
            self._quality_cards[preset.key] = card
        self._choose_quality(DEFAULT_QUALITY)

        divider = tk.Frame(opt_inner, bg=p["border"], height=1)
        divider.pack(fill="x", pady=(0, 11))
        format_row = tk.Frame(opt_inner, bg=p["card"])
        format_row.pack(fill="x")
        tk.Label(
            format_row,
            text="采样率",
            font=(UI_FONT, 11),
            bg=p["card"],
            fg=p["secondary"],
        ).pack(side="left")
        # Use a small mapping layer so the Chinese labels stay readable.
        self.rate_combo = ttk.Combobox(
            format_row,
            values=["48 kHz（视频）", "44.1 kHz（音乐）"],
            state="readonly",
            width=15,
            font=(UI_FONT, 11),
        )
        self.rate_combo.current(0 if DEFAULT_SAMPLE_RATE == 48000 else 1)
        self.rate_combo.pack(side="left", padx=(6, 18))
        tk.Label(
            format_row,
            text="位深",
            font=(UI_FONT, 11),
            bg=p["card"],
            fg=p["secondary"],
        ).pack(side="left")
        self.depth_combo = ttk.Combobox(
            format_row,
            values=["24-bit PCM（推荐）", "32-bit float"],
            state="readonly",
            width=19,
            font=(UI_FONT, 11),
        )
        self.depth_combo.current(0 if DEFAULT_BIT_DEPTH == "24" else 1)
        self.depth_combo.pack(side="left", padx=(6, 16))
        self.guitar_check = tk.Checkbutton(
            format_row,
            text="保留单独吉他轨",
            variable=self.guitar_var,
            font=(UI_FONT, 11),
            bg=p["card"],
            fg=p["secondary"],
            activebackground=p["card"],
            activeforeground=p["text"],
            selectcolor=p["card"],
            highlightthickness=0,
            bd=0,
        )
        self.guitar_check.pack(side="left")
        self._controls.extend(
            [self.rate_combo, self.depth_combo, self.guitar_check]
        )

        # ---- action / progress
        action = tk.Frame(outer, bg=p["bg"])
        action.pack(fill="x", pady=(14, 0))
        status_box = tk.Frame(action, bg=p["bg"])
        status_box.pack(side="left", fill="x", expand=True)
        tk.Label(
            status_box,
            textvariable=self.status_var,
            font=(UI_FONT, 15, "bold"),
            bg=p["bg"],
            fg=p["text"],
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            status_box,
            textvariable=self.detail_var,
            font=(UI_FONT, 10),
            bg=p["bg"],
            fg=p["secondary"],
            anchor="w",
            justify="left",
            wraplength=500,
        ).pack(anchor="w", pady=(3, 0))
        buttons = tk.Frame(action, bg=p["bg"])
        buttons.pack(side="right")
        self.cancel_btn = TextButton(
            buttons,
            "取消",
            self._cancel,
            p,
            height=34,
            width=74,
        )
        self.cancel_btn.pack(side="left", padx=(0, 8))
        self.cancel_btn.pack_forget()
        self.start_btn = AccentButton(
            buttons,
            "开始分离",
            self._start,
            p,
            width=176,
            height=46,
        )
        self.start_btn.pack(side="left")

        progress_holder = tk.Frame(outer, bg=p["bg"])
        progress_holder.pack(fill="x", pady=(13, 0))
        self.progress = ttk.Progressbar(
            progress_holder,
            variable=self.progress_var,
            maximum=100.0,
            style="MusicCut.Horizontal.TProgressbar",
        )
        self.progress.pack(fill="x")

        footer = tk.Frame(outer, bg=p["bg"])
        footer.pack(fill="x", side="bottom", pady=(12, 0))
        tk.Label(
            footer,
            text="全程离线处理 · 音频不会上传 · 模型 Demucs v4（MIT）",
            font=(UI_FONT, 9),
            bg=p["bg"],
            fg=p["tertiary"],
        ).pack(side="left")
        self.device_footer = tk.Label(
            footer,
            text="AUTO",
            font=(UI_FONT, 9),
            bg=p["bg"],
            fg=p["tertiary"],
        )
        self.device_footer.pack(side="right")
        self.start_btn.set_enabled(False)

    # ------------------------------------------------------------------ actions
    def _choose_quality(self, value: str) -> None:
        if self._busy:
            return
        self.quality_var.set(value)
        for key, card in self._quality_cards.items():
            card.set_selected(key == value)

    def _browse_audio(self) -> None:
        if self._busy:
            return
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择音乐",
            filetypes=[
                ("音频文件", "*.mp3 *.wav"),
                ("MP3", "*.mp3"),
                ("WAV", "*.wav"),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            self._select_path(Path(path))

    def _select_path(self, path: Path) -> None:
        path = path.expanduser()
        if not path.exists() or not path.is_file():
            messagebox.showerror(APP_NAME, f"找不到文件：\n{path}", parent=self.root)
            return
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            messagebox.showerror(
                APP_NAME,
                "只支持 MP3 和 WAV 文件。\n\n"
                "如果原文件是其他格式，请先在剪辑软件或 ffmpeg 中转换为 WAV。",
                parent=self.root,
            )
            return
        self.selected_file = path
        self.file_name_label.configure(text=path.name)
        self.file_meta_label.configure(text=self._file_meta(path))
        self._update_output_hint()
        self.status_var.set("准备就绪")
        self.detail_var.set("点击“开始分离”，会生成人声、鼓、贝斯、钢琴、其他乐器五条分轨。")
        self.start_btn.set_enabled(True)

    def _file_meta(self, path: Path) -> str:
        try:
            info = sf.info(str(path))
            duration = info.frames / info.samplerate if info.samplerate else 0
            channels = "立体声" if info.channels == 2 else "单声道"
            if info.channels > 2:
                channels = f"{info.channels} 声道"
            fmt = info.format or path.suffix.lstrip(".").upper()
            return (
                f"{fmt} · {_fmt_time(duration)} · "
                f"{info.samplerate / 1000:g} kHz · {channels} · "
                f"{_fmt_size(path.stat().st_size)}"
            )
        except Exception:
            return "音频信息读取失败，仍可尝试分离（会自动使用 FFmpeg）"

    def _browse_output(self) -> None:
        if self._busy:
            return
        initial = Path(self.output_root_var.get()).expanduser()
        if not initial.exists():
            initial = Path.home()
        path = filedialog.askdirectory(
            parent=self.root,
            title="选择输出根目录",
            initialdir=str(initial),
            mustexist=False,
        )
        if path:
            self.output_root_var.set(path)

    def _update_output_hint(self) -> None:
        raw = self.output_root_var.get().strip()
        if not raw:
            self.output_hint_var.set("")
            return
        try:
            expanded = Path(raw).expanduser()
        except Exception:
            self.output_hint_var.set("")
            return
        # Keep the user's original notation (~, relative) in the hint. It is only
        # expanded when the job starts, and short paths read better in the UI.
        display = raw.rstrip("/")
        if self.selected_file:
            stem_dir = f"{self.selected_file.stem}_stems"
            self.output_hint_var.set(f"将创建文件夹：{display}/{stem_dir}")
        else:
            self.output_hint_var.set(
                f"每个音频会生成独立文件夹（<歌名>_stems）：{display}"
            )

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.start_btn.set_enabled(not busy and self.selected_file is not None)
        if busy:
            self.cancel_btn.pack(side="left", padx=(0, 8))
        else:
            self.cancel_btn.pack_forget()
        for widget in self._controls:
            try:
                if isinstance(widget, ttk.Combobox):
                    widget.configure(state="disabled" if busy else "readonly")
                elif isinstance(widget, tk.Checkbutton):
                    widget.configure(state="disabled" if busy else "normal")
                else:
                    widget.configure(state="disabled" if busy else "normal")
            except tk.TclError:
                pass
        for button in self._text_buttons:
            button.set_enabled(not busy)
        if busy:
            self.start_btn.set_text("分离中…")
        else:
            self.start_btn.set_text("开始分离")
        self.cancel_btn.set_enabled(True)

    def _start(self) -> None:
        if self._busy:
            return
        if self.selected_file is None:
            messagebox.showinfo(
                APP_NAME, "请先选择一个 MP3 或 WAV 文件。", parent=self.root
            )
            return
        root_text = self.output_root_var.get().strip()
        if not root_text:
            messagebox.showinfo(APP_NAME, "请选择输出位置。", parent=self.root)
            return
        output_root = Path(root_text).expanduser()
        try:
            output_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(
                APP_NAME, f"无法创建输出文件夹：\n{exc}", parent=self.root
            )
            return

        rate = 48000 if self.rate_combo.current() == 0 else 44100
        depth = "24" if self.depth_combo.current() == 0 else "32f"
        self._close_result_dialog()
        self._cancel_event = threading.Event()
        self._queue = queue.Queue()
        self._job_started = time.monotonic()
        self.progress_var.set(0.0)
        self.status_var.set("准备中")
        self.detail_var.set("正在检查音频与模型…")
        self.device_footer.configure(text="模型加载中")
        self._set_busy(True)
        self.start_btn.set_enabled(False)
        args = dict(
            source=self.selected_file,
            output_root=output_root,
            quality=self.quality_var.get(),
            sample_rate=rate,
            bit_depth=depth,
            keep_guitar=self.guitar_var.get(),
            cancel_event=self._cancel_event,
        )
        self._worker = threading.Thread(
            target=self._job, kwargs=args, name="musiccut-separation", daemon=True
        )
        self._worker.start()
        self.root.after(80, self._poll_queue)

    def _enqueue_progress(self, update: ProgressUpdate) -> None:
        self._queue.put(("progress", update))

    def _job(
        self,
        *,
        source: Path,
        output_root: Path,
        quality: str,
        sample_rate: int,
        bit_depth: str,
        keep_guitar: bool,
        cancel_event: threading.Event,
    ) -> None:
        try:
            if self._separator is None:
                self._separator = StemSeparator(
                    device="auto", cancel_event=cancel_event
                )
                self._queue.put(("ready", self._separator.device_label))
            else:
                self._separator.set_cancel_event(cancel_event)
            result = self._separator.separate_file(
                source,
                output_root=output_root,
                quality=quality,
                sample_rate=sample_rate,
                bit_depth=bit_depth,
                keep_guitar=keep_guitar,
                progress=self._enqueue_progress,
            )
            self._queue.put(("done", result))
        except SeparationCancelled:
            self._queue.put(("cancelled", None))
        except MusicCutError as exc:
            self._queue.put(("error", str(exc)))
        except Exception as exc:  # pragma: no cover - last-resort UI safety net
            logger.exception("Unexpected GUI separation failure")
            self._queue.put(
                (
                    "error",
                    f"发生未预期错误：{exc}\n\n详细日志：logs/musiccut.log",
                )
            )

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "progress":
                    self._on_progress(payload)
                elif kind == "ready":
                    self.device_footer.configure(text=str(payload).upper())
                    self.badge_var.set(f"Demucs v4 · {payload}")
                elif kind == "done":
                    self._on_done(payload)
                elif kind == "cancelled":
                    self._on_cancelled()
                elif kind == "error":
                    self._on_error(str(payload))
        except queue.Empty:
            pass
        if self._busy:
            self.root.after(100, self._poll_queue)

    def _on_progress(self, update: ProgressUpdate) -> None:
        self.progress_var.set(update.fraction * 100.0)
        labels = {
            "loading": "准备中",
            "separating": "AI 分离中",
            "writing": "正在导出分轨",
            "done": "完成",
        }
        self.status_var.set(labels.get(update.stage, "处理中"))
        elapsed = time.monotonic() - self._job_started
        detail = update.message
        if 0.06 < update.fraction < 0.995:
            eta = elapsed * (1.0 - update.fraction) / max(update.fraction, 1e-6)
            if update.stage in {"separating", "writing"} and eta >= 3:
                detail += f"  ·  已用 {_fmt_time(elapsed)} · 剩余约 {_fmt_time(eta)}"
            else:
                detail += f"  ·  已用 {_fmt_time(elapsed)}"
        self.detail_var.set(detail)

    def _on_done(self, result: SeparationResult) -> None:
        self._set_busy(False)
        self.progress_var.set(100.0)
        self.status_var.set("分轨完成")
        self.detail_var.set(
            f"已导出 {len(result.stems)} 条分轨 · 用时 {_fmt_time(result.elapsed_seconds)}"
            f" · {result.sample_rate / 1000:g} kHz / "
            f"{'32-bit float' if result.bit_depth == '32f' else '24-bit PCM'}"
        )
        self._show_result_dialog(result)

    def _on_cancelled(self) -> None:
        self._set_busy(False)
        self.progress_var.set(0.0)
        self.status_var.set("已取消")
        self.detail_var.set("没有生成完整的分轨。可以调整设置后重新开始。")

    def _on_error(self, message: str) -> None:
        self._set_busy(False)
        self.progress_var.set(0.0)
        self.status_var.set("遇到问题")
        self.detail_var.set("任务未完成，音频没有被上传。")
        messagebox.showerror(APP_NAME, message, parent=self.root)

    def _cancel(self) -> None:
        if not self._busy or self._cancel_event is None:
            return
        self._cancel_event.set()
        self.cancel_btn.configure(state="disabled")
        self.status_var.set("正在取消…")
        self.detail_var.set("会在当前分块处理完成后停止。")

    def _open_output_root(self) -> None:
        path = Path(self.output_root_var.get()).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(["open", str(path)])
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"无法打开输出文件夹：\n{exc}", parent=self.root)

    def _open_result_folder(self, path: Path) -> None:
        try:
            subprocess.Popen(["open", str(path)])
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"无法打开文件夹：\n{exc}", parent=self.root)

    # ------------------------------------------------------------------- dialogs
    def _show_result_dialog(self, result: SeparationResult) -> None:
        self._close_result_dialog()
        p = self.p
        dialog = tk.Toplevel(self.root)
        self._result_dialog = dialog
        dialog.title("分轨完成")
        dialog.configure(bg=p["bg"])
        dialog.resizable(False, False)
        dialog.transient(self.root)
        width, height = 650, 520
        self.root.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - width) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - height) // 3)
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        dialog.grab_set()
        dialog.bind("<Escape>", lambda _event: dialog.destroy())

        body = tk.Frame(dialog, bg=p["bg"])
        body.pack(fill="both", expand=True, padx=28, pady=24)
        button_row = tk.Frame(body, bg=p["bg"])
        button_row.pack(side="bottom", fill="x", pady=(12, 0))
        TextButton(
            button_row,
            "在访达中显示",
            lambda: self._open_result_folder(result.output_dir),
            p,
            kind="primary",
            height=38,
            width=132,
        ).pack(side="right")
        TextButton(
            button_row,
            "关闭",
            dialog.destroy,
            p,
            height=38,
            width=78,
        ).pack(side="right", padx=(0, 8))
        content = tk.Frame(body, bg=p["bg"])
        content.pack(side="top", fill="both", expand=True)
        tk.Label(
            content,
            text="✓",
            font=(UI_FONT, 24, "bold"),
            bg=p["bg"],
            fg=p["success"],
        ).pack(anchor="w")
        tk.Label(
            content,
            text=f"{len(result.stems)} 条分轨已导出",
            font=(UI_FONT, 20, "bold"),
            bg=p["bg"],
            fg=p["text"],
        ).pack(anchor="w", pady=(2, 4))
        tk.Label(
            content,
            text=f"{result.duration:.1f} 秒 · {result.sample_rate / 1000:g} kHz · "
            f"{'32-bit float' if result.bit_depth == '32f' else '24-bit PCM'} · "
            f"{result.device_label}",
            font=(UI_FONT, 11),
            bg=p["bg"],
            fg=p["secondary"],
        ).pack(anchor="w")
        tk.Label(
            content,
            text=str(result.output_dir),
            font=(UI_FONT, 11),
            bg=p["bg"],
            fg=p["text"],
            wraplength=560,
            justify="left",
        ).pack(anchor="w", pady=(10, 10))

        list_frame = tk.Frame(
            content,
            bg=p["card"],
            highlightthickness=1,
            highlightbackground=p["border"],
        )
        list_frame.pack(fill="x")
        for index, stem in enumerate(result.stems):
            row = tk.Frame(list_frame, bg=p["card"])
            row.pack(fill="x", padx=14, pady=5)
            tk.Label(
                row,
                text=stem.label,
                font=(UI_FONT, 11, "bold"),
                bg=p["card"],
                fg=p["text"],
                width=7,
                anchor="w",
            ).pack(side="left")
            tk.Label(
                row,
                text=stem.path.name,
                font=("Menlo", 10),
                bg=p["card"],
                fg=p["secondary"],
                anchor="w",
            ).pack(side="left")
            tk.Label(
                row,
                text=f"{_fmt_size(stem.size_bytes)}  ·  {stem.peak_dbfs:.1f} dBFS",
                font=(UI_FONT, 10),
                bg=p["card"],
                fg=p["tertiary"],
            ).pack(side="right")

        if result.peak_guard_gain_db < 0:
            tk.Label(
                content,
                text=f"为避免削波，所有分轨统一降低了 {abs(result.peak_guard_gain_db):.2f} dB（相对比例不变）。",
                font=(UI_FONT, 9),
                bg=p["bg"],
                fg=p["secondary"],
                anchor="w",
            ).pack(fill="x", pady=(7, 0))
        tk.Label(
            content,
            text="提示：钢琴轨是模型最难的一轨，密集编曲中如有串音，可稍作剪辑或衰减。",
            font=(UI_FONT, 9),
            bg=p["bg"],
            fg=p["tertiary"],
            anchor="w",
            wraplength=560,
            justify="left",
        ).pack(fill="x", pady=(6, 0))

    def _close_result_dialog(self) -> None:
        if self._result_dialog is not None:
            try:
                self._result_dialog.destroy()
            except tk.TclError:
                pass
            self._result_dialog = None

    def _about(self) -> None:
        messagebox.showinfo(
            f"关于 {APP_NAME}",
            f"{APP_NAME} {APP_VERSION}\n\n"
            "本地五轨音乐分离工具\n"
            f"模型：{MODEL_LABEL}\n"
            "模型许可：MIT（Demucs v4）\n\n"
            "音频只在本机处理，不会上传。",
            parent=self.root,
        )

    def _on_tk_exception(self, exc_type, exc_value, exc_tb) -> None:
        logger.exception("Tk callback exception", exc_info=(exc_type, exc_value, exc_tb))
        if not self._busy:
            messagebox.showerror(APP_NAME, f"界面错误：{exc_value}", parent=self.root)

    def _on_close(self) -> None:
        if self._busy:
            if not messagebox.askyesno(
                APP_NAME,
                "分离任务仍在运行。退出会中断处理，已生成的分轨可能不完整。\n\n确定退出吗？",
                parent=self.root,
            ):
                return
            if self._cancel_event is not None:
                self._cancel_event.set()
        self._close_result_dialog()
        self.root.destroy()


def _configure_logging() -> None:
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / "musiccut.log",
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(handler)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    _configure_logging()
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"无法启动图形界面：{exc}", file=sys.stderr)
        return 1
    initial = args[0] if args else None
    app = MusicCutApp(root, initial_file=initial)
    try:
        root.mainloop()
    finally:
        if app._cancel_event is not None:
            app._cancel_event.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
