from __future__ import annotations

import enum
import json
import os
import time
import uuid
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk
from typing import Optional

try:
    import winsound
except ImportError:
    winsound = None


DATA_FILE = Path.home() / ".dynamic_pomodoro.json"
HISTORY_LIMIT = 100
_BASE_H = 430
_EXPANDED_H = 645


class AppState(enum.Enum):
    IDLE = "idle"
    WORKING = "working"
    PAUSED = "paused"
    BREAK = "break"


# ── Persistence ───────────────────────────────────────────────────────────────

class SessionStore:
    def __init__(self, path: Path = DATA_FILE) -> None:
        self.path = path
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {"sessions": [], "top_score": None, "top_continuous": None, "settings": {}}

    def _save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except OSError:
            pass

    def add_session(
        self, work_seconds: float, break_seconds: float, longest_stint_seconds: float
    ) -> None:
        session = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now().isoformat(),
            "work_seconds": work_seconds,
            "break_seconds": break_seconds,
            "longest_stint_seconds": longest_stint_seconds,
            "valid": True,
        }
        self._data["sessions"].append(session)
        self._update_top_score(session)
        self._update_top_continuous(session)
        self._save()

    # -- top_score (best total session work time) --------------------------------

    def _update_top_score(self, new_session: dict) -> None:
        top = self._data.get("top_score")
        if top is None or not top.get("valid"):
            best = self._best_valid_session("work_seconds")
            if best:
                self._data["top_score"] = _score_record(best["work_seconds"], best["timestamp"])
        elif new_session["work_seconds"] > top["work_seconds"]:
            self._data["top_score"] = _score_record(new_session["work_seconds"], new_session["timestamp"])

    def invalidate_top_score(self) -> None:
        if self._data.get("top_score"):
            self._data["top_score"]["valid"] = False
        self._save()

    def recalculate_top_score(self) -> None:
        best = self._best_valid_session("work_seconds")
        self._data["top_score"] = (
            _score_record(best["work_seconds"], best["timestamp"]) if best else None
        )
        self._save()

    def get_top_score(self) -> Optional[dict]:
        top = self._data.get("top_score")
        return top if (top and top.get("valid")) else None

    # -- top_continuous (best single uninterrupted work stint) ------------------

    def _update_top_continuous(self, new_session: dict) -> None:
        stint = new_session.get("longest_stint_seconds")
        if stint is None:
            return
        top = self._data.get("top_continuous")
        if top is None or not top.get("valid"):
            best = self._best_valid_session("longest_stint_seconds")
            if best and best.get("longest_stint_seconds") is not None:
                self._data["top_continuous"] = _score_record(
                    best["longest_stint_seconds"], best["timestamp"]
                )
        elif stint > top["work_seconds"]:
            self._data["top_continuous"] = _score_record(stint, new_session["timestamp"])

    def invalidate_top_continuous(self) -> None:
        if self._data.get("top_continuous"):
            self._data["top_continuous"]["valid"] = False
        self._save()

    def recalculate_top_continuous(self) -> None:
        best = self._best_valid_session("longest_stint_seconds")
        if best and best.get("longest_stint_seconds") is not None:
            self._data["top_continuous"] = _score_record(
                best["longest_stint_seconds"], best["timestamp"]
            )
        else:
            self._data["top_continuous"] = None
        self._save()

    def get_top_continuous(self) -> Optional[dict]:
        top = self._data.get("top_continuous")
        return top if (top and top.get("valid")) else None

    # -- shared helpers ---------------------------------------------------------

    def _best_valid_session(self, key: str) -> Optional[dict]:
        valid = [
            s for s in self._data["sessions"]
            if s.get("valid") and s.get(key) is not None
        ]
        return max(valid, key=lambda s: s[key], default=None)

    def invalidate_session(self, session_id: str) -> None:
        for s in self._data["sessions"]:
            if s["id"] == session_id:
                s["valid"] = False
                break
        self._save()

    def recalculate_all(self) -> None:
        self.recalculate_top_score()
        self.recalculate_top_continuous()

    def get_recent_sessions(self, limit: int = HISTORY_LIMIT) -> list[dict]:
        return list(reversed(self._data["sessions"]))[:limit]

    def get_setting(self, key: str, default=None):
        return self._data.get("settings", {}).get(key, default)

    def set_setting(self, key: str, value) -> None:
        if "settings" not in self._data:
            self._data["settings"] = {}
        self._data["settings"][key] = value
        self._save()


def _score_record(work_seconds: float, timestamp: str) -> dict:
    return {"work_seconds": work_seconds, "timestamp": timestamp, "valid": True}


# ── Main App ──────────────────────────────────────────────────────────────────

class DynamicPomodoro:
    FRACTION_OPTIONS = {
        "1/6": 1 / 6,
        "1/5": 1 / 5,
        "1/4": 1 / 4,
        "1/3": 1 / 3,
        "1/2": 1 / 2,
    }

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Dynamic Pomodoro")

        ttk.Style().theme_use("clam")
        self._default_bg: str = root.cget("bg")

        self.root.geometry(f"480x{_BASE_H}")
        self.root.minsize(480, _BASE_H)

        self.store = SessionStore()
        self.state = AppState.IDLE
        self.work_elapsed_seconds = 0.0
        self.break_accumulated_seconds = 0.0
        self._work_start_tick: float | None = None
        self._break_end_tick: float | None = None
        self._timer_id: str | None = None
        self._history_expanded = False
        self._longest_stint_seconds = 0.0
        self._text_fg = "#1a1a1a"
        self._muted_fg = "#666666"

        self.selected_fraction = tk.StringVar(value="1/5")
        self.work_display = tk.StringVar(value="00:00")
        self.break_display = tk.StringVar(value="00:00")
        self.status_display = tk.StringVar(value="Ready")
        self.top_score_display = tk.StringVar(value="--:--")
        self.top_score_date_display = tk.StringVar(value="")
        self.top_continuous_display = tk.StringVar(value="--:--")
        self.top_continuous_date_display = tk.StringVar(value="")

        self._build_ui()
        self._render_state()
        self._refresh_top_score_display()
        self._refresh_top_continuous_display()

    # ── Styling ───────────────────────────────────────────────────────────────

    def _resolve_color(self, color: str) -> str:
        try:
            r, g, b = [c >> 8 for c in self.root.winfo_rgb(color)]
            return f"#{r:02x}{g:02x}{b:02x}"
        except tk.TclError:
            return "#d9d9d9"

    def _blend_white(self, color: str, alpha: float) -> str:
        h = self._resolve_color(color).lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return "#{:02x}{:02x}{:02x}".format(
            int(255 * alpha + r * (1 - alpha)),
            int(255 * alpha + g * (1 - alpha)),
            int(255 * alpha + b * (1 - alpha)),
        )

    def _is_dark(self, color: str) -> bool:
        h = self._resolve_color(color).lstrip("#")
        r, g, b = int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
        return (0.299 * r + 0.587 * g + 0.114 * b) < 0.5

    def _apply_widget_styles(self, bg: str) -> None:
        dark = self._is_dark(bg)
        self._text_fg = "#ffffff" if dark else "#1a1a1a"
        self._muted_fg = "#cccccc" if dark else "#666666"
        green_fg = "#7ddd7d" if dark else "#2a7a2a"
        run_fg = "#7ab4ff" if dark else "#1a5a8a"

        btn = self._blend_white(bg, 0.20)
        btn_active = self._blend_white(bg, 0.60)

        style = ttk.Style()
        for name in ("TFrame", "TLabel", "TNotebook", "TNotebook.Tab",
                     "TLabelframe", "TLabelframe.Label"):
            style.configure(name, background=bg, foreground=self._text_fg)
        style.configure("TButton", background=btn, borderwidth=1, relief="raised",
                        foreground=self._text_fg)
        style.map("TButton", background=[("active", btn_active), ("pressed", bg)])
        style.configure("TRadiobutton", background=bg, foreground=self._text_fg)
        style.map("TRadiobutton", background=[("active", bg)])
        style.configure("TCombobox", fieldbackground=btn, foreground=self._text_fg)
        style.configure("Muted.TLabel", background=bg, foreground=self._muted_fg)
        style.configure("Green.TLabel", background=bg, foreground=green_fg)
        style.configure("Run.TLabel", background=bg, foreground=run_fg)

    def _apply_background(self, color: str) -> None:
        try:
            self.root.configure(bg=color)
            self._apply_widget_styles(color)
        except tk.TclError:
            pass

    def _reset_background(self) -> None:
        try:
            self.root.configure(bg=self._default_bg)
            self._apply_widget_styles(self._default_bg)
        except tk.TclError:
            pass

    # ── UI: top-level ─────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        active_bg = self.store.get_setting("bg_color", "") or self._default_bg
        if active_bg != self._default_bg:
            self.root.configure(bg=active_bg)
        self._apply_widget_styles(active_bg)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.timer_tab = ttk.Frame(self.notebook)
        self.settings_tab = ttk.Frame(self.notebook, padding=16)
        self.notebook.add(self.timer_tab, text="Timer")
        self.notebook.add(self.settings_tab, text="Settings")

        self._build_timer_tab()
        self._build_settings_tab()

    # ── UI: timer tab ─────────────────────────────────────────────────────────

    def _build_timer_tab(self) -> None:
        main_area = ttk.Frame(self.timer_tab, padding=(16, 12, 16, 4))
        main_area.pack(fill=tk.X)

        # Left: controls
        controls = ttk.Frame(main_area)
        controls.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        ttk.Label(controls, text="Work Stopwatch", font=("Segoe UI", 12, "bold")).pack(anchor=tk.W)
        ttk.Label(controls, textvariable=self.work_display, font=("Consolas", 34, "bold")).pack(
            anchor=tk.W, pady=(2, 12)
        )

        fraction_row = ttk.Frame(controls)
        fraction_row.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(fraction_row, text="Break Fraction:").pack(side=tk.LEFT)
        self.fraction_combo = ttk.Combobox(
            fraction_row,
            textvariable=self.selected_fraction,
            values=list(self.FRACTION_OPTIONS.keys()),
            state="readonly",
            width=8,
        )
        self.fraction_combo.pack(side=tk.LEFT, padx=(8, 0))
        self.fraction_combo.bind("<<ComboboxSelected>>", self._on_fraction_change)

        ttk.Label(controls, text="Available Break", font=("Segoe UI", 12, "bold")).pack(anchor=tk.W)
        ttk.Label(controls, textvariable=self.break_display, font=("Consolas", 26, "bold")).pack(
            anchor=tk.W, pady=(2, 10)
        )

        ttk.Label(controls, textvariable=self.status_display).pack(
            anchor=tk.W, pady=(0, 10)
        )

        button_row = ttk.Frame(controls)
        button_row.pack(fill=tk.X)
        self.primary_button = ttk.Button(button_row)
        self.primary_button.pack(side=tk.LEFT)
        self.secondary_button = ttk.Button(button_row)
        self.secondary_button.pack(side=tk.LEFT, padx=(8, 0))
        self.reset_button = ttk.Button(button_row, text="Reset Session", command=self.reset_session)
        self.reset_button.pack(side=tk.RIGHT)

        # Right: top score records
        score_frame = ttk.Frame(main_area)
        score_frame.pack(side=tk.RIGHT, anchor=tk.NE, padx=(16, 0))

        ttk.Label(score_frame, text="Best Total Session", font=("Segoe UI", 8, "bold")).pack(anchor=tk.E)
        ttk.Label(
            score_frame,
            textvariable=self.top_score_display,
            font=("Consolas", 16, "bold"),
            style="Green.TLabel",
        ).pack(anchor=tk.E)
        ttk.Label(
            score_frame,
            textvariable=self.top_score_date_display,
            font=("Segoe UI", 8),
            style="Muted.TLabel",
        ).pack(anchor=tk.E)

        ttk.Separator(score_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(6, 4))

        ttk.Label(score_frame, text="Best Work Run", font=("Segoe UI", 8, "bold")).pack(anchor=tk.E)
        ttk.Label(
            score_frame,
            textvariable=self.top_continuous_display,
            font=("Consolas", 16, "bold"),
            style="Run.TLabel",
        ).pack(anchor=tk.E)
        ttk.Label(
            score_frame,
            textvariable=self.top_continuous_date_display,
            font=("Segoe UI", 8),
            style="Muted.TLabel",
        ).pack(anchor=tk.E)

        ttk.Separator(self.timer_tab, orient=tk.HORIZONTAL).pack(fill=tk.X)

        self.history_toggle_btn = ttk.Button(
            self.timer_tab,
            text=self._history_toggle_label(),
            command=self._toggle_history,
        )
        self.history_toggle_btn.pack(fill=tk.X, ipady=3)

        self.history_frame = ttk.Frame(self.timer_tab)

    # ── UI: history panel ─────────────────────────────────────────────────────

    def _history_toggle_label(self) -> str:
        n = len(self.store.get_recent_sessions(HISTORY_LIMIT))
        arrow = "▼" if self._history_expanded else "▶"
        return f"{arrow}  Session History  ({n})"

    def _toggle_history(self) -> None:
        self._history_expanded = not self._history_expanded
        if self._history_expanded:
            self._rebuild_history_panel()
            self.history_frame.pack(fill=tk.BOTH, expand=True)
            self.root.geometry(f"480x{_EXPANDED_H}")
            self.root.minsize(480, _EXPANDED_H)
        else:
            self.history_frame.pack_forget()
            self.root.geometry(f"480x{_BASE_H}")
            self.root.minsize(480, _BASE_H)
        self.history_toggle_btn.configure(text=self._history_toggle_label())

    def _rebuild_history_panel(self) -> None:
        for w in self.history_frame.winfo_children():
            w.destroy()

        canvas = tk.Canvas(self.history_frame, height=200, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.history_frame, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        inner = ttk.Frame(canvas)
        win_id = canvas.create_window((0, 0), window=inner, anchor=tk.NW)

        for col, (text, w) in enumerate([
            ("Date / Time", 18), ("Work Time", 10), ("Break Time", 10), ("", 9)
        ]):
            ttk.Label(inner, text=text, font=("Segoe UI", 9, "bold"), width=w).grid(
                row=0, column=col, padx=4, pady=2, sticky=tk.W
            )
        ttk.Separator(inner, orient=tk.HORIZONTAL).grid(row=1, column=0, columnspan=4, sticky=tk.EW, pady=2)

        sessions = self.store.get_recent_sessions(HISTORY_LIMIT)
        for grid_row, session in enumerate(sessions, start=2):
            valid = session.get("valid", True)
            fg = self._text_fg if valid else self._muted_fg

            try:
                dt = datetime.fromisoformat(session.get("timestamp", ""))
                ts_str = dt.strftime("%Y-%m-%d  %H:%M")
            except ValueError:
                ts_str = session.get("timestamp", "")[:16]

            ttk.Label(inner, text=ts_str, foreground=fg, width=18).grid(row=grid_row, column=0, padx=4, pady=1, sticky=tk.W)
            ttk.Label(inner, text=self._format_time(session.get("work_seconds", 0)), foreground=fg, width=10).grid(row=grid_row, column=1, padx=4, pady=1)
            ttk.Label(inner, text=self._format_time(session.get("break_seconds", 0)), foreground=fg, width=10).grid(row=grid_row, column=2, padx=4, pady=1)

            if valid:
                sid = session["id"]
                ttk.Button(
                    inner, text="Invalidate", width=9,
                    command=lambda s=sid: self._invalidate_session(s),
                ).grid(row=grid_row, column=3, padx=4, pady=1)
            else:
                ttk.Label(inner, text="(invalid)", foreground=self._muted_fg, width=9).grid(row=grid_row, column=3, padx=4, pady=1)

        if not sessions:
            ttk.Label(inner, text="No sessions recorded yet.", foreground=self._muted_fg).grid(
                row=2, column=0, columnspan=4, padx=8, pady=8
            )

        def _on_inner_configure(_):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(e):
            canvas.itemconfig(win_id, width=e.width)

        def _on_mousewheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # Sync canvas bg to current theme bg
        try:
            canvas.configure(bg=self.root.cget("bg"))
        except tk.TclError:
            pass

    def _invalidate_session(self, session_id: str) -> None:
        if not messagebox.askyesno("Invalidate Session", "Mark this session as invalid?"):
            return
        self.store.invalidate_session(session_id)
        self.store.recalculate_all()
        self._refresh_top_score_display()
        self._refresh_top_score_settings_label()
        self._refresh_top_continuous_display()
        self._refresh_top_continuous_settings_label()
        self._rebuild_history_panel()
        self.history_toggle_btn.configure(text=self._history_toggle_label())

    # ── UI: settings tab ──────────────────────────────────────────────────────

    def _build_settings_tab(self) -> None:
        # Background color
        bg_frame = ttk.LabelFrame(self.settings_tab, text="Background Color", padding=8)
        bg_frame.pack(fill=tk.X, pady=(0, 12))

        saved_color = self.store.get_setting("bg_color", "")
        preview_bg = saved_color if saved_color else self._default_bg
        self.bg_color_preview = tk.Label(bg_frame, width=4, bg=preview_bg, relief=tk.SUNKEN)
        self.bg_color_preview.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(bg_frame, text="Pick Color…", command=self._pick_bg_color).pack(side=tk.LEFT)
        ttk.Button(bg_frame, text="Reset to Default", command=self._reset_bg_color).pack(side=tk.LEFT, padx=(8, 0))

        # Sound
        sound_frame = ttk.LabelFrame(self.settings_tab, text="Break-End Notification Sound", padding=8)
        sound_frame.pack(fill=tk.X, pady=(0, 12))

        self.sound_mode = tk.StringVar(value=self.store.get_setting("sound_mode", "beep"))
        for value, label in [("beep", "Default Beep"), ("none", "No Sound"), ("file", "Custom Audio File (.wav)")]:
            ttk.Radiobutton(
                sound_frame, text=label,
                variable=self.sound_mode, value=value,
                command=self._on_sound_mode_change,
            ).pack(anchor=tk.W)

        file_row = ttk.Frame(sound_frame)
        file_row.pack(fill=tk.X, pady=(6, 0))
        self.sound_file_label = ttk.Label(
            file_row,
            text=self._truncate_path(self.store.get_setting("sound_file", "")),
            style="Muted.TLabel",
            width=32,
        )
        self.sound_file_label.pack(side=tk.LEFT)
        self.sound_browse_btn = ttk.Button(file_row, text="Browse…", command=self._browse_sound_file)
        self.sound_browse_btn.pack(side=tk.LEFT, padx=(8, 0))
        self._on_sound_mode_change()

        # Top score management
        score_frame = ttk.LabelFrame(self.settings_tab, text="Top Score Management", padding=8)
        score_frame.pack(fill=tk.X)

        # Best Total Session
        ts_row = ttk.Frame(score_frame)
        ts_row.pack(fill=tk.X, pady=(0, 2))
        self.top_score_info_label = ttk.Label(ts_row, text="")
        self.top_score_info_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(ts_row, text="Invalidate", command=self._invalidate_top_score).pack(side=tk.RIGHT)
        self._refresh_top_score_settings_label()

        ttk.Separator(score_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)

        # Best Work Run
        tc_row = ttk.Frame(score_frame)
        tc_row.pack(fill=tk.X, pady=(0, 2))
        self.top_continuous_info_label = ttk.Label(tc_row, text="")
        self.top_continuous_info_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(tc_row, text="Invalidate", command=self._invalidate_top_continuous).pack(side=tk.RIGHT)
        self._refresh_top_continuous_settings_label()

    # ── Top score (best total session) ────────────────────────────────────────

    def _refresh_top_score_display(self) -> None:
        top = self.store.get_top_score()
        if top:
            self.top_score_display.set(self._format_time(top["work_seconds"]))
            try:
                self.top_score_date_display.set(
                    datetime.fromisoformat(top["timestamp"]).strftime("%Y-%m-%d")
                )
            except ValueError:
                self.top_score_date_display.set(top["timestamp"][:10])
        else:
            self.top_score_display.set("--:--")
            self.top_score_date_display.set("")

    def _refresh_top_score_settings_label(self) -> None:
        top = self.store.get_top_score()
        if top:
            try:
                date_str = datetime.fromisoformat(top["timestamp"]).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                date_str = top["timestamp"][:16]
            self.top_score_info_label.configure(
                text=f"Best Total Session: {self._format_time(top['work_seconds'])} on {date_str}"
            )
        else:
            self.top_score_info_label.configure(text="Best Total Session: none recorded")

    def _invalidate_top_score(self) -> None:
        if not self.store.get_top_score():
            messagebox.showinfo("Top Score", "No valid best total session to invalidate.")
            return
        if not messagebox.askyesno("Invalidate", "Invalidate the best total session record?"):
            return
        self.store.invalidate_top_score()
        self._refresh_top_score_display()
        self._refresh_top_score_settings_label()

    # ── Top continuous (best work run) ───────────────────────────────────────

    def _refresh_top_continuous_display(self) -> None:
        top = self.store.get_top_continuous()
        if top:
            self.top_continuous_display.set(self._format_time(top["work_seconds"]))
            try:
                self.top_continuous_date_display.set(
                    datetime.fromisoformat(top["timestamp"]).strftime("%Y-%m-%d")
                )
            except ValueError:
                self.top_continuous_date_display.set(top["timestamp"][:10])
        else:
            self.top_continuous_display.set("--:--")
            self.top_continuous_date_display.set("")

    def _refresh_top_continuous_settings_label(self) -> None:
        top = self.store.get_top_continuous()
        if top:
            try:
                date_str = datetime.fromisoformat(top["timestamp"]).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                date_str = top["timestamp"][:16]
            self.top_continuous_info_label.configure(
                text=f"Best Work Run: {self._format_time(top['work_seconds'])} on {date_str}"
            )
        else:
            self.top_continuous_info_label.configure(text="Best Work Run: none recorded")

    def _invalidate_top_continuous(self) -> None:
        if not self.store.get_top_continuous():
            messagebox.showinfo("Top Score", "No valid best work run to invalidate.")
            return
        if not messagebox.askyesno("Invalidate", "Invalidate the best work run record?"):
            return
        self.store.invalidate_top_continuous()
        self._refresh_top_continuous_display()
        self._refresh_top_continuous_settings_label()

    # ── Settings actions ──────────────────────────────────────────────────────

    def _pick_bg_color(self) -> None:
        current = self.store.get_setting("bg_color", "") or self._default_bg
        result = colorchooser.askcolor(color=current, title="Choose Background Color")
        if result and result[1]:
            color = result[1]
            self.store.set_setting("bg_color", color)
            self.bg_color_preview.configure(bg=color)
            self._apply_background(color)

    def _reset_bg_color(self) -> None:
        self.store.set_setting("bg_color", "")
        self.bg_color_preview.configure(bg=self._default_bg)
        self._reset_background()

    def _on_sound_mode_change(self) -> None:
        mode = self.sound_mode.get()
        self.store.set_setting("sound_mode", mode)
        self.sound_browse_btn.configure(state="normal" if mode == "file" else "disabled")

    def _browse_sound_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Audio File",
            filetypes=[("WAV files", "*.wav"), ("All files", "*.*")],
        )
        if path:
            self.store.set_setting("sound_file", path)
            self.sound_file_label.configure(text=self._truncate_path(path))

    @staticmethod
    def _truncate_path(path: str, max_len: int = 38) -> str:
        if not path:
            return "(no file selected)"
        return path if len(path) <= max_len else "…" + path[-(max_len - 1):]

    # ── Notification ──────────────────────────────────────────────────────────

    def _notify_break_finished(self) -> None:
        mode = self.store.get_setting("sound_mode", "beep")
        file = self.store.get_setting("sound_file", "")

        if mode == "beep" and winsound is not None:
            try:
                winsound.Beep(880, 300)
                winsound.Beep(1100, 300)
            except RuntimeError:
                pass
        elif mode == "file" and file and os.path.exists(file) and winsound is not None:
            try:
                winsound.PlaySound(file, winsound.SND_FILENAME | winsound.SND_ASYNC)
            except Exception:
                pass

        messagebox.showinfo("Break Over", "Break time is over. Ready to work again.")

    # ── Session lifecycle ─────────────────────────────────────────────────────

    @property
    def break_fraction(self) -> float:
        return self.FRACTION_OPTIONS[self.selected_fraction.get()]

    def _on_fraction_change(self, _: tk.Event) -> None:
        if self.state == AppState.WORKING:
            return
        self._update_status_text()

    def _schedule_tick(self, delay_ms: int = 200) -> None:
        self._cancel_tick()
        self._timer_id = self.root.after(delay_ms, self._tick)

    def _cancel_tick(self) -> None:
        if self._timer_id is not None:
            self.root.after_cancel(self._timer_id)
            self._timer_id = None

    def _tick(self) -> None:
        self._timer_id = None
        if self.state == AppState.WORKING:
            self._update_work_display()
            self._schedule_tick(200)
            return
        if self.state == AppState.BREAK:
            remaining = self._current_break_remaining_seconds()
            self.break_accumulated_seconds = remaining
            self.break_display.set(self._format_time(remaining))
            if remaining <= 0:
                self.break_accumulated_seconds = 0.0
                self._notify_break_finished()
                self.state = AppState.PAUSED
                self._break_end_tick = None
                self._render_state()
            else:
                self._schedule_tick(200)

    def _current_work_elapsed_seconds(self) -> float:
        if self.state != AppState.WORKING or self._work_start_tick is None:
            return self.work_elapsed_seconds
        return self.work_elapsed_seconds + (time.monotonic() - self._work_start_tick)

    def _current_break_remaining_seconds(self) -> float:
        if self.state != AppState.BREAK or self._break_end_tick is None:
            return self.break_accumulated_seconds
        return max(0.0, self._break_end_tick - time.monotonic())

    def _current_longest_stint_seconds(self) -> float:
        if self.state == AppState.WORKING and self._work_start_tick is not None:
            return max(self._longest_stint_seconds, time.monotonic() - self._work_start_tick)
        return self._longest_stint_seconds

    def _update_work_display(self) -> None:
        self.work_display.set(self._format_time(self._current_work_elapsed_seconds()))

    def _update_break_display(self) -> None:
        if self.state == AppState.BREAK:
            self.break_display.set(self._format_time(self._current_break_remaining_seconds()))
            return
        self.break_display.set(self._format_time(self.break_accumulated_seconds))

    def _update_status_text(self) -> None:
        ratio = self.selected_fraction.get()
        if self.state == AppState.IDLE:
            self.status_display.set(f"Ready to work. Break ratio: {ratio}")
        elif self.state == AppState.WORKING:
            self.status_display.set(f"Working... Break accrues at {ratio} of your work time")
        elif self.state == AppState.PAUSED:
            self.status_display.set("Work paused. Resume work or start your break.")
        elif self.state == AppState.BREAK:
            self.status_display.set("On break. You can end break early to carry leftovers.")

    def _render_state(self) -> None:
        self._update_work_display()
        self._update_break_display()
        self._update_status_text()

        self.fraction_combo.configure(state="disabled" if self.state == AppState.WORKING else "readonly")

        if self.state == AppState.IDLE:
            self.primary_button.configure(text="Start Work", command=self.start_work)
            self.secondary_button.pack_forget()

        elif self.state == AppState.WORKING:
            self.primary_button.configure(text="Stop Working", command=self.pause_work)
            self.secondary_button.pack_forget()

        elif self.state == AppState.PAUSED:
            resume_label = "Continue Working" if self.work_elapsed_seconds > 0 else "Start Work"
            self.primary_button.configure(text=resume_label, command=self.start_work)
            self.secondary_button.configure(text="Start Break", command=self.start_break)
            if not self.secondary_button.winfo_ismapped():
                self.secondary_button.pack(side=tk.LEFT, padx=(8, 0))
            self.secondary_button.state(
                ["disabled"] if self.break_accumulated_seconds <= 0.5 else ["!disabled"]
            )

        elif self.state == AppState.BREAK:
            self.primary_button.configure(text="End Break Early", command=self.end_break_early)
            self.secondary_button.pack_forget()

    def start_work(self) -> None:
        if self.state not in (AppState.IDLE, AppState.PAUSED):
            return
        self.state = AppState.WORKING
        self._work_start_tick = time.monotonic()
        self._render_state()
        self._schedule_tick(200)

    def pause_work(self) -> None:
        if self.state != AppState.WORKING or self._work_start_tick is None:
            return
        elapsed = time.monotonic() - self._work_start_tick
        self.work_elapsed_seconds += elapsed
        self.break_accumulated_seconds += elapsed * self.break_fraction
        if elapsed > self._longest_stint_seconds:
            self._longest_stint_seconds = elapsed
        self._work_start_tick = None
        self._cancel_tick()
        self.state = AppState.PAUSED
        self._render_state()

    def start_break(self) -> None:
        if self.state != AppState.PAUSED or self.break_accumulated_seconds <= 0:
            return
        self.state = AppState.BREAK
        self._break_end_tick = time.monotonic() + self.break_accumulated_seconds
        self._render_state()
        self._schedule_tick(200)

    def end_break_early(self) -> None:
        if self.state != AppState.BREAK:
            return
        self.break_accumulated_seconds = self._current_break_remaining_seconds()
        self._break_end_tick = None
        self._cancel_tick()
        self.state = AppState.PAUSED
        self._render_state()

    def reset_session(self) -> None:
        if self.state == AppState.WORKING:
            if not messagebox.askyesno("Reset Session", "Reset while work is running?"):
                return
        if self.state == AppState.BREAK:
            if not messagebox.askyesno("Reset Session", "Reset while break timer is running?"):
                return

        work = self._current_work_elapsed_seconds()
        if work > 1.0:
            longest_stint = self._current_longest_stint_seconds()
            self.store.add_session(work, self.break_accumulated_seconds, longest_stint)
            self._refresh_top_score_display()
            self._refresh_top_score_settings_label()
            self._refresh_top_continuous_display()
            self._refresh_top_continuous_settings_label()
            self.history_toggle_btn.configure(text=self._history_toggle_label())
            if self._history_expanded:
                self._rebuild_history_panel()

        self._cancel_tick()
        self.state = AppState.IDLE
        self.work_elapsed_seconds = 0.0
        self.break_accumulated_seconds = 0.0
        self._work_start_tick = None
        self._break_end_tick = None
        self._longest_stint_seconds = 0.0
        self._render_state()

    @staticmethod
    def _format_time(total_seconds: float) -> str:
        seconds = max(0, int(round(total_seconds)))
        hours, rem = divmod(seconds, 3600)
        minutes, secs = divmod(rem, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"


def main() -> None:
    root = tk.Tk()
    DynamicPomodoro(root)
    root.mainloop()


if __name__ == "__main__":
    main()
