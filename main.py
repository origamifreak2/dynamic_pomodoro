from __future__ import annotations

import enum
import time
import tkinter as tk
from tkinter import messagebox, ttk

try:
    import winsound
except ImportError:  # pragma: no cover
    winsound = None


class AppState(enum.Enum):
    IDLE = "idle"
    WORKING = "working"
    PAUSED = "paused"
    BREAK = "break"


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
        self.root.geometry("430x320")
        self.root.minsize(430, 320)

        self.state = AppState.IDLE

        self.work_elapsed_seconds = 0.0
        self.break_accumulated_seconds = 0.0

        self._work_start_tick: float | None = None
        self._break_end_tick: float | None = None
        self._timer_id: str | None = None

        self.selected_fraction = tk.StringVar(value="1/5")
        self.work_display = tk.StringVar(value="00:00")
        self.break_display = tk.StringVar(value="00:00")
        self.status_display = tk.StringVar(value="Ready")

        self._build_ui()
        self._render_state()

    @property
    def break_fraction(self) -> float:
        return self.FRACTION_OPTIONS[self.selected_fraction.get()]

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill=tk.BOTH, expand=True)

        ttk.Label(container, text="Work Stopwatch", font=("Segoe UI", 12, "bold")).pack(anchor=tk.W)
        ttk.Label(container, textvariable=self.work_display, font=("Consolas", 34, "bold")).pack(
            pady=(2, 12)
        )

        fraction_row = ttk.Frame(container)
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

        ttk.Label(container, text="Available Break", font=("Segoe UI", 12, "bold")).pack(anchor=tk.W)
        ttk.Label(container, textvariable=self.break_display, font=("Consolas", 26, "bold")).pack(
            pady=(2, 10)
        )

        ttk.Label(container, textvariable=self.status_display, foreground="#333333").pack(anchor=tk.W, pady=(0, 10))

        button_row = ttk.Frame(container)
        button_row.pack(fill=tk.X)

        self.primary_button = ttk.Button(button_row)
        self.primary_button.pack(side=tk.LEFT)

        self.secondary_button = ttk.Button(button_row)
        self.secondary_button.pack(side=tk.LEFT, padx=(8, 0))

        self.reset_button = ttk.Button(button_row, text="Reset Session", command=self.reset_session)
        self.reset_button.pack(side=tk.RIGHT)

    def _on_fraction_change(self, _event: tk.Event) -> None:
        if self.state == AppState.WORKING:
            # Selector is disabled while working, but this keeps behavior safe.
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

        combo_state = "readonly"
        if self.state == AppState.WORKING:
            combo_state = "disabled"
        self.fraction_combo.configure(state=combo_state)

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

            if self.break_accumulated_seconds <= 0.5:
                self.secondary_button.state(["disabled"])
            else:
                self.secondary_button.state(["!disabled"])

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

        elapsed_since_start = time.monotonic() - self._work_start_tick
        self.work_elapsed_seconds += elapsed_since_start
        self.break_accumulated_seconds += elapsed_since_start * self.break_fraction

        self._work_start_tick = None
        self._cancel_tick()
        self.state = AppState.PAUSED
        self._render_state()

    def start_break(self) -> None:
        if self.state != AppState.PAUSED:
            return
        if self.break_accumulated_seconds <= 0:
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

        self._cancel_tick()
        self.state = AppState.IDLE
        self.work_elapsed_seconds = 0.0
        self.break_accumulated_seconds = 0.0
        self._work_start_tick = None
        self._break_end_tick = None
        self._render_state()

    def _notify_break_finished(self) -> None:
        if winsound is not None:
            try:
                winsound.Beep(880, 300)
                winsound.Beep(1100, 300)
            except RuntimeError:
                pass

        messagebox.showinfo("Break Over", "Break time is over. Ready to work again.")

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