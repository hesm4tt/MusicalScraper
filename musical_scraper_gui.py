#!/usr/bin/env python3
"""Musical Scraper - desktop front end.

A GUI over musical_scraper.run_job. All the real work happens in a worker thread;
tkinter is only ever touched from the main thread, via a queue the UI drains on a
timer, because tkinter is not thread-safe.

The widgets are hand-drawn on canvases rather than ttk. macOS renders native
buttons and checkboxes and ignores any colour you set on them, so a warm theme is
only possible by drawing the controls ourselves.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import traceback
import webbrowser
from datetime import datetime, timezone

import tkinter as tk
from tkinter import filedialog, messagebox

import musical_scraper as core

APP_NAME = "Musical Scraper"
TAGLINE = "Grab a TikTok profile - no watermark, oldest first"
DEFAULT_CUTOFF = "2018-08-01"            # musical.ly folded into TikTok around here

# Warm musical.ly-ish palette: red through orange into amber.
RED = "#F02D3A"
ORANGE = "#FF6B1A"
AMBER = "#FFB627"
CREAM = "#FFF6EC"
CARD = "#FFFFFF"
INK = "#3A1A0F"
MUTED = "#A98874"
LINE = "#F2DCC9"
LOGBG = "#2B1206"
LOGFG = "#FFE9D2"


def ui_font(size=13, bold=False):
    family = ("Helvetica Neue" if sys.platform == "darwin"
              else "Segoe UI" if os.name == "nt" else "DejaVu Sans")
    return (family, size, "bold") if bold else (family, size)


def mono_font(size=11):
    family = ("Menlo" if sys.platform == "darwin"
              else "Consolas" if os.name == "nt" else "DejaVu Sans Mono")
    return (family, size)


def mix(c1, c2, t):
    """Blend two #rrggbb colours."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def round_rect(cv, x0, y0, x1, y1, r, **kw):
    """Rounded rectangle as a smoothed polygon - Tk has no native one."""
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return cv.create_polygon(pts, smooth=True, **kw)


def default_output_root():
    """Somewhere sane and absolute.

    A bundled .app launches with the working directory at '/', so a relative
    default would try to write to the filesystem root.
    """
    home = os.path.expanduser("~")
    downloads = os.path.join(home, "Downloads")
    return os.path.join(downloads if os.path.isdir(downloads) else home, APP_NAME)


class Pill(tk.Canvas):
    """A rounded, coloured button that looks the same on every platform."""

    def __init__(self, master, text, command, kind="primary", width=132, height=40):
        super().__init__(master, width=width, height=height, highlightthickness=0,
                         bd=0, bg=CREAM, cursor="hand2")
        self.command, self.kind = command, kind
        self.w, self.h, self.text = width, height, text
        self._enabled = True
        self._hover = False
        self._draw()
        self.bind("<Button-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))

    def _colours(self):
        if not self._enabled:
            return "#EADFD5", MUTED, None
        if self.kind == "primary":
            base = mix(RED, ORANGE, 0.45)
            return (mix(base, "#000000", 0.10) if self._hover else base), "#FFFFFF", None
        if self.kind == "danger":
            base = "#8A3A20"
            return (mix(base, "#000000", 0.12) if self._hover else base), "#FFFFFF", None
        fill = mix(CREAM, ORANGE, 0.14) if self._hover else CARD
        return fill, INK, ORANGE

    def _draw(self, dy=0):
        self.delete("all")
        fill, fg, outline = self._colours()
        round_rect(self, 2, 2 + dy, self.w - 2, self.h - 2 + dy, 13,
                   fill=fill, outline=outline or fill, width=2)
        self.create_text(self.w / 2, self.h / 2 + dy, text=self.text,
                         fill=fg, font=ui_font(13, bold=True))

    def _set_hover(self, on):
        self._hover = on
        self._draw()

    def _press(self, _):
        if self._enabled:
            self._draw(dy=1)

    def _release(self, _):
        self._draw()
        if self._enabled and self.command:
            self.command()

    def configure_state(self, enabled):
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()


class Check(tk.Canvas):
    """Hand-drawn checkbox - macOS ignores colours on the native one."""

    BOX = 20

    def __init__(self, master, text, variable, width=560):
        super().__init__(master, width=width, height=26, highlightthickness=0,
                         bd=0, bg=CREAM, cursor="hand2")
        self.var, self.text = variable, text
        self._draw()
        self.bind("<Button-1>", self._toggle)
        variable.trace_add("write", lambda *_: self._draw())

    def _draw(self):
        self.delete("all")
        on = bool(self.var.get())
        b = self.BOX
        round_rect(self, 2, 3, 2 + b, 3 + b, 6,
                   fill=mix(RED, ORANGE, 0.45) if on else CARD,
                   outline=ORANGE if on else LINE, width=2)
        if on:
            self.create_line(8, 13, 11, 17, 17, 8, fill="#FFFFFF", width=2.5,
                             capstyle="round", joinstyle="round")
        self.create_text(b + 12, 14, text=self.text, anchor="w",
                         fill=INK, font=ui_font(12))

    def _toggle(self, _):
        self.var.set(not self.var.get())


class Bar(tk.Canvas):
    """Gradient progress bar."""

    def __init__(self, master, height=12):
        super().__init__(master, height=height, highlightthickness=0, bd=0, bg=CREAM)
        self.h = height
        self.value, self.maximum = 0, 100
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, value, maximum):
        self.value, self.maximum = value, max(1, maximum)
        self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width() or 1
        round_rect(self, 0, 0, w, self.h, self.h / 2, fill="#F0E0D2", outline="#F0E0D2")
        frac = min(1.0, self.value / self.maximum)
        if frac <= 0:
            return
        end = max(self.h, w * frac)
        for x in range(int(end)):          # red -> amber sweep
            self.create_line(x, 0, x, self.h, fill=mix(RED, AMBER, x / max(1, w)))
        # mask the ends back into a pill
        self.create_arc(-self.h, 0, self.h, self.h, start=90, extent=180,
                        fill=CREAM, outline=CREAM)


class ScraperApp(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=CREAM)
        self.pack(fill="both", expand=True)
        self.events = queue.Queue()
        self.stop_flag = threading.Event()
        self.worker = None
        self.last_out_dir = None

        self._header()
        body = tk.Frame(self, bg=CREAM, padx=22, pady=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(4, weight=1)
        self._form(body)
        self._settings(body)
        self._actions(body)
        self._progress(body)
        self._log(body)
        self.after(80, self._drain)

    # ---------------- layout ----------------

    def _header(self):
        h = 86
        cv = tk.Canvas(self, height=h, highlightthickness=0, bd=0, bg=RED)
        cv.pack(fill="x")

        def paint(_=None):
            cv.delete("all")
            w = cv.winfo_width() or 900
            for x in range(w):             # red -> orange -> amber
                t = x / max(1, w - 1)
                col = mix(RED, ORANGE, t * 2) if t < 0.5 else mix(ORANGE, AMBER, (t - 0.5) * 2)
                cv.create_line(x, 0, x, h, fill=col)
            cv.create_text(26, h / 2 - 11, text="♫  " + APP_NAME, anchor="w",
                           fill="#FFFFFF", font=ui_font(23, bold=True))
            cv.create_text(30, h / 2 + 16, text=TAGLINE, anchor="w",
                           fill="#FFF0DF", font=ui_font(12))
        cv.bind("<Configure>", paint)

    def _labelled(self, parent, row, label, var, hint=None, button=None):
        tk.Label(parent, text=label, bg=CREAM, fg=INK, font=ui_font(12, bold=True)
                 ).grid(row=row, column=0, sticky="w", pady=(8, 2))
        holder = tk.Frame(parent, bg=LINE, padx=2, pady=2)
        holder.grid(row=row + 1, column=0, sticky="ew")
        holder.columnconfigure(0, weight=1)
        entry = tk.Entry(holder, textvariable=var, font=ui_font(13), bd=0,
                         bg=CARD, fg=INK, insertbackground=ORANGE,
                         relief="flat", highlightthickness=0)
        entry.grid(row=0, column=0, sticky="ew", ipady=7, padx=(8, 4))
        if button:
            Pill(holder, button[0], button[1], kind="ghost", width=96, height=32
                 ).grid(row=0, column=1, padx=(2, 2))
        if hint:
            tk.Label(parent, text=hint, bg=CREAM, fg=MUTED, font=ui_font(11)
                     ).grid(row=row + 2, column=0, sticky="w", pady=(2, 0))
        return entry

    def _form(self, p):
        box = tk.Frame(p, bg=CREAM)
        box.grid(row=0, column=0, sticky="ew")
        box.columnconfigure(0, weight=1)
        self.username = tk.StringVar()
        e = self._labelled(box, 0, "TikTok username", self.username,
                           hint="e.g. lorengray   (the @ is optional)")
        e.focus_set()
        self.outdir = tk.StringVar(value=default_output_root())
        self._labelled(box, 3, "Save to", self.outdir,
                       button=("Browse", self._pick_folder))
        self.extra_tag = tk.StringVar()
        self._labelled(box, 6, "Extra hashtag (optional)", self.extra_tag,
                       hint="added to every filename, e.g. MyArchive")

    def _settings(self, p):
        box = tk.Frame(p, bg=CREAM)
        box.grid(row=1, column=0, sticky="ew", pady=(14, 0))

        row = tk.Frame(box, bg=CREAM)
        row.pack(anchor="w", pady=(0, 6))
        self.mode = tk.StringVar(value="batch")
        for val, label in (("batch", "Next"), ("all", "Everything")):
            tk.Radiobutton(row, text=label, variable=self.mode, value=val,
                           bg=CREAM, fg=INK, activebackground=CREAM,
                           selectcolor=CARD, font=ui_font(12), bd=0,
                           highlightthickness=0).pack(side="left")
            if val == "batch":
                self.batch_size = tk.StringVar(value="25")
                tk.Spinbox(row, from_=1, to=99999, width=6, bd=0, relief="flat",
                           textvariable=self.batch_size, font=ui_font(12),
                           bg=CARD, fg=INK, highlightthickness=1,
                           highlightbackground=LINE).pack(side="left", padx=6)
                tk.Label(row, text="oldest not yet downloaded", bg=CREAM, fg=INK,
                         font=ui_font(12)).pack(side="left", padx=(0, 14))

        self.use_cutoff = tk.BooleanVar(value=False)
        cut = tk.Frame(box, bg=CREAM)
        cut.pack(anchor="w")
        Check(cut, "Only videos posted before", self.use_cutoff, width=200
              ).pack(side="left")
        self.cutoff = tk.StringVar(value=DEFAULT_CUTOFF)
        tk.Entry(cut, textvariable=self.cutoff, width=12, bd=0, relief="flat",
                 bg=CARD, fg=INK, font=ui_font(12), highlightthickness=1,
                 highlightbackground=LINE).pack(side="left", ipady=3)
        tk.Label(cut, text="  (musical.ly era ended around Aug 2018)", bg=CREAM,
                 fg=MUTED, font=ui_font(11)).pack(side="left")

        self.number = tk.BooleanVar(value=False)
        self.best = tk.BooleanVar(value=False)
        self.refresh = tk.BooleanVar(value=False)
        Check(box, "Number the files 0001, 0002, ... in upload order", self.number).pack(anchor="w")
        Check(box, "Prefer highest resolution (H.265 - some players can't read it)",
              self.best).pack(anchor="w")
        Check(box, "Rescan the profile (a list under 24h old is reused otherwise)",
              self.refresh).pack(anchor="w")

    def _actions(self, p):
        bar = tk.Frame(p, bg=CREAM)
        bar.grid(row=2, column=0, sticky="ew", pady=14)
        self.preview_btn = Pill(bar, "Preview", self.on_preview, kind="ghost")
        self.preview_btn.pack(side="left")
        self.download_btn = Pill(bar, "Download", self.on_download, kind="primary")
        self.download_btn.pack(side="left", padx=8)
        self.stop_btn = Pill(bar, "Stop", self.on_stop, kind="danger", width=92)
        self.stop_btn.pack(side="left")
        self.stop_btn.configure_state(False)
        self.open_btn = Pill(bar, "Open folder", self.on_open, kind="ghost")
        self.open_btn.pack(side="left", padx=8)
        self.open_btn.configure_state(False)

    def _progress(self, p):
        wrap = tk.Frame(p, bg=CREAM)
        wrap.grid(row=3, column=0, sticky="ew")
        wrap.columnconfigure(0, weight=1)
        self.bar = Bar(wrap)
        self.bar.grid(row=0, column=0, sticky="ew")
        self.status = tk.StringVar(value="Enter a username to begin.")
        tk.Label(wrap, textvariable=self.status, bg=CREAM, fg=INK,
                 font=ui_font(12)).grid(row=1, column=0, sticky="w", pady=(8, 0))

    def _log(self, p):
        wrap = tk.Frame(p, bg=LOGBG, padx=2, pady=2)
        wrap.grid(row=4, column=0, sticky="nsew", pady=(12, 0))
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        self.log = tk.Text(wrap, height=12, wrap="none", bd=0, relief="flat",
                           bg=LOGBG, fg=LOGFG, insertbackground=AMBER,
                           font=mono_font(11), highlightthickness=0, padx=10, pady=8)
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = tk.Scrollbar(wrap, command=self.log.yview, bd=0, highlightthickness=0)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set, state="disabled")
        self.log.tag_configure("ok", foreground="#8FE388")
        self.log.tag_configure("bad", foreground="#FF8A6B")
        self.log.tag_configure("head", foreground=AMBER)

    # ---------------- helpers ----------------

    def _pick_folder(self):
        chosen = filedialog.askdirectory(initialdir=self.outdir.get() or "~")
        if chosen:
            self.outdir.set(chosen)

    def _write(self, text):
        tag = ("bad" if "FAILED" in text else
               "head" if text.strip().startswith(("Scanning", "Found", "Downloading",
                                                  "Preview", "Done")) else "")
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _busy(self, running):
        self.preview_btn.configure_state(not running)
        self.download_btn.configure_state(not running)
        self.stop_btn.configure_state(running)
        if not running and self.last_out_dir:
            self.open_btn.configure_state(True)

    # ---------------- actions ----------------

    def _build_config(self, dry_run):
        handle = core.handle_from_target(self.username.get().strip())
        if not handle:
            messagebox.showwarning(APP_NAME, "Please enter a TikTok username.")
            return None
        root = self.outdir.get().strip() or default_output_root()
        until = None
        if self.use_cutoff.get():
            try:
                until = datetime.strptime(self.cutoff.get().strip(),
                                          "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                messagebox.showwarning(APP_NAME, "Date must look like 2018-08-01.")
                return None
        limit = None
        if self.mode.get() == "batch":
            try:
                limit = max(1, int(self.batch_size.get()))
            except ValueError:
                limit = 25
        tags = [t for t in [self.extra_tag.get().strip().lstrip("#")] if t]
        return core.JobConfig(
            handle, out=os.path.join(os.path.expanduser(root), handle),
            tag=tags, limit=limit, until=until, number=self.number.get(),
            codec="best" if self.best.get() else "h264",
            refresh=self.refresh.get(), dry_run=dry_run)

    def on_preview(self):
        self._start(True)

    def on_download(self):
        self._start(False)

    def on_stop(self):
        self.stop_flag.set()
        self.status.set("Stopping after the current downloads finish...")

    def on_open(self):
        if not self.last_out_dir:
            return
        if sys.platform == "darwin":
            os.system(f'open "{self.last_out_dir}"')
        elif os.name == "nt":
            os.startfile(self.last_out_dir)          # noqa: S606
        else:
            webbrowser.open("file://" + self.last_out_dir)

    def _start(self, dry_run):
        if self.worker and self.worker.is_alive():
            return
        cfg = self._build_config(dry_run)
        if cfg is None:
            return
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.bar.set(0, 100)
        self.stop_flag.clear()
        self._busy(True)
        self.status.set("Working...")

        def emit(kind, payload):
            self.events.put((kind, payload))

        def run():
            try:
                emit("done", core.run_job(
                    cfg,
                    log=lambda m: emit("log", m),
                    status=lambda m: emit("status", m),
                    progress=lambda d, t: emit("progress", (d, t)),
                    should_stop=self.stop_flag.is_set))
            except core.JobError as exc:
                emit("error", str(exc))
            except Exception:
                emit("error", traceback.format_exc(limit=3))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    # ---------------- event pump ----------------

    def _drain(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._write(payload)
                elif kind == "status":
                    self.status.set(payload)
                elif kind == "progress":
                    done, total = payload
                    self.bar.set(done, total)
                    self.status.set(f"Downloaded {done} of {total}")
                elif kind == "error":
                    self._busy(False)
                    self.status.set("Something went wrong.")
                    self._write(payload)
                    messagebox.showerror(APP_NAME, payload)
                elif kind == "done":
                    self._finish(payload)
        except queue.Empty:
            pass
        self.after(80, self._drain)

    def _finish(self, summary):
        self._busy(False)
        self.last_out_dir = summary.get("out_dir")
        self.open_btn.configure_state(True)
        if summary.get("cancelled"):
            self.status.set("Stopped. Run it again to pick up where you left off.")
        elif not summary.get("downloaded") and not summary.get("failed"):
            self.status.set(f"{len(summary.get('planned') or [])} video(s) ready.")
        else:
            msg = f"Finished: {summary.get('downloaded', 0)} downloaded"
            if summary.get("failed"):
                msg += f", {summary['failed']} failed"
            self.status.set(msg)


def main():
    # Dual purpose: double-clicking launches the GUI, but passing arguments runs
    # the command line inside the same bundle. Handy for power users, and it means
    # the shipped binary can be smoke-tested end to end.
    # (On Windows the build is windowed, so CLI output has no console to go to -
    # use the Python source there instead.)
    if len(sys.argv) > 1:
        sys.exit(core.main(sys.argv[1:]))

    root = tk.Tk()
    root.title(APP_NAME)
    root.geometry("860x830")
    root.minsize(760, 700)
    root.configure(bg=CREAM)
    ScraperApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
