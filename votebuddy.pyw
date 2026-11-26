"""VoteBuddy - Minecraft server vote reminder / launcher.

Keeps a list of vote links, tracks the cooldown for each one, and when a
vote is due it opens the page in your default browser. The VoteBuddy
Autofill extension (see extension folder) types your username in; you tick
the captcha and click vote. After each page opens, a small pop-up asks if
you voted and lets you set how long until the next vote.
"""
import json
import os
import queue
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote
import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

CHROME_PATHS = [
    os.path.join(os.environ.get(v, ""), r"Google\Chrome\Application\chrome.exe")
    for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
]


def open_in_browser(url):
    """Open vote pages in Chrome (where the Autofill extension lives)."""
    for path in CHROME_PATHS:
        if os.path.isfile(path):
            import subprocess
            subprocess.Popen([path, url])
            return
    webbrowser.open_new_tab(url)


FROZEN = getattr(sys, "frozen", False)  # True when running as VoteBuddy.exe
APP_DIR = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))
# Each person's list lives in their own %APPDATA%\VoteBuddy, so the .exe can
# sit anywhere (Downloads, Desktop...) and updates never wipe it.
DATA_DIR = os.path.join(os.environ.get("APPDATA") or APP_DIR, "VoteBuddy")
DATA_FILE = os.path.join(DATA_DIR, "votes.json")
OLD_DATA_FILE = os.path.join(APP_DIR, "votes.json")  # where older versions saved
def resource(name):
    """Files bundled inside the .exe (icon) or next to the script."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


ICON = resource("votebuddy.ico")


def set_icon(win):
    # CTk puts its own icon on windows ~200ms after creation; replace it after that
    if os.path.exists(ICON):
        win.after(300, lambda: win.winfo_exists() and win.iconbitmap(ICON))


STARTUP_DIR = os.path.join(os.environ.get("APPDATA", ""),
                           r"Microsoft\Windows\Start Menu\Programs\Startup")
STARTUP_FILE = os.path.join(STARTUP_DIR, "VoteBuddy.bat")

RADIUS = 18          # roundness of buttons ("bubble" look)
GREEN = ("#16a34a", "#22c55e")
GREEN_HOVER = ("#15803d", "#16a34a")
GRAY_BTN = ("#d4d4dc", "#3a3a46")
GRAY_HOVER = ("#c2c2cc", "#4a4a58")
GRAY_TEXT = ("#1f1f24", "#e8e8ee")
MUTED = ("#6b6b76", "#9a9aa6")


def load():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(DATA_FILE) and os.path.exists(OLD_DATA_FILE):
        import shutil
        shutil.copy2(OLD_DATA_FILE, DATA_FILE)  # bring over a list from an older version
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data.setdefault("username", "")
    data.setdefault("auto_open", False)
    data.setdefault("autofill", True)
    data.setdefault("autosubmit", True)
    data.setdefault("theme", "dark")
    data.setdefault("sites", [])
    data.setdefault("phone_on", False)
    data.setdefault("phone_topic", "")
    for s in data["sites"]:
        s.setdefault("id", uuid.uuid4().hex[:10])  # lets the extension say which site
    return data


# ---- timer reports from the browser extension ----
# The extension reads "vote again in 23h 14m" off the page and POSTs it here.
# Only listens on this PC (127.0.0.1) and only accepts requests coming from
# a browser extension, so websites can't send fake timers.
PORT = 47615
REPORTS = queue.Queue()


class ReportHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        origin = self.headers.get("Origin", "")
        if self.path != "/cooldown" or not origin.startswith(("chrome-extension://", "moz-extension://")):
            self.send_response(403)
            self.end_headers()
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(min(n, 2000)))
            site, secs = str(body["site"])[:40], float(body["seconds"])
            if not 60 <= secs <= 7 * 86400:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            self.send_response(400)
            self.end_headers()
            return
        REPORTS.put((site, secs))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


def start_report_server():
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), ReportHandler)
    except OSError:
        return False  # port busy (VoteBuddy already open?) - timers just won't sync
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return True


# ---- phone alerts (ntfy) ----
# ntfy.sh is a free push-notification service with iPhone and Android apps.
# The phone subscribes to a private random "topic" name; VoteBuddy posts to it.
NTFY_SERVER = "https://ntfy.sh"


def new_topic():
    import secrets
    abc = "abcdefghjkmnpqrstuvwxyz23456789"  # no look-alike letters, easy to type
    return "votebuddy-" + "".join(secrets.choice(abc) for _ in range(10))


def ssl_contexts():
    """certifi's certificate list first (some PCs have an outdated Windows list), then Windows'."""
    import ssl
    out = []
    try:
        import certifi
        out.append(ssl.create_default_context(cafile=certifi.where()))
    except Exception:
        pass
    out.append(ssl.create_default_context())
    return out


def send_phone(topic, title, message, links=(), done=None):
    """Push a notification to the phone in the background.
    links: [(label, url)] - first one opens when the notification is tapped.
    done(error_or_None) runs on the worker thread, so it must only set simple values."""
    import urllib.request
    body = {"topic": topic, "title": title, "message": message, "priority": 4,
            "tags": ["ballot_box_with_check"]}
    links = [(l[:40], u) for l, u in links if u.startswith(("http://", "https://"))]
    if links:
        body["click"] = links[0][1]
        body["actions"] = [{"action": "view", "label": l, "url": u, "clear": True}
                           for l, u in links[:3]]  # ntfy allows 3 buttons

    def work():
        err = None
        for ctx in ssl_contexts():
            try:
                req = urllib.request.Request(NTFY_SERVER, data=json.dumps(body).encode("utf-8"),
                                             headers={"Content-Type": "application/json",
                                                      "User-Agent": "VoteBuddy"})
                with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                    r.read()
                err = None
                break
            except Exception as e:  # no internet, server down, bad certificates, ...
                err = str(e) or e.__class__.__name__
        if done:
            done(err)
    threading.Thread(target=work, daemon=True).start()


def save(data):
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, DATA_FILE)


def fmt_left(seconds):
    if seconds <= 0:
        return "READY"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"


def bubble(master, text, command, kind="normal", **kw):
    """Rounded button. kind: normal (blue), go (green), soft (gray)."""
    colors = {}
    if kind == "go":
        colors = {"fg_color": GREEN, "hover_color": GREEN_HOVER}
    elif kind == "soft":
        colors = {"fg_color": GRAY_BTN, "hover_color": GRAY_HOVER, "text_color": GRAY_TEXT}
    kw.setdefault("height", 36)
    return ctk.CTkButton(master, text=text, command=command, corner_radius=RADIUS,
                         font=ctk.CTkFont(size=13, weight="bold"), **colors, **kw)


def bring_up(win):
    """CTk pop-ups sometimes open behind the main window; pull them forward."""
    win.after(120, lambda: (win.lift(), win.focus_force()))


class Popup(ctk.CTkToplevel):
    """Dark-mode friendly message box. Returns the clicked button's text."""

    def __init__(self, master, text, buttons=("OK",), title="VoteBuddy"):
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.choice = None
        ctk.CTkLabel(self, text=text, font=ctk.CTkFont(size=14), wraplength=340,
                     justify="left").pack(padx=24, pady=(22, 14))
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(padx=20, pady=(0, 18), anchor="e")
        for n, b in enumerate(buttons):
            kind = "normal" if n == len(buttons) - 1 else "soft"
            bubble(row, b, lambda b=b: self.pick(b), kind=kind, width=90).pack(side="left", padx=4)
        self.transient(master)
        bring_up(self)
        self.after(150, self.grab_set)
        self.wait_window()

    def pick(self, b):
        self.choice = b
        self.destroy()


class Stepper(ctk.CTkFrame):
    """[-] number [+] with a unit label."""

    def __init__(self, master, value, lo, hi, unit, step=1):
        super().__init__(master, fg_color="transparent")
        self.lo, self.hi, self.step = lo, hi, step
        self.var = tk.StringVar(value=str(value))
        bubble(self, "−", lambda: self.bump(-1), kind="soft", width=36).pack(side="left")
        ctk.CTkEntry(self, textvariable=self.var, width=52, justify="center", corner_radius=RADIUS,
                     font=ctk.CTkFont(size=15, weight="bold")).pack(side="left", padx=4)
        bubble(self, "+", lambda: self.bump(1), kind="soft", width=36).pack(side="left")
        ctk.CTkLabel(self, text=unit, text_color=MUTED).pack(side="left", padx=(6, 0))

    def get(self):
        try:
            v = int(float(self.var.get()))
        except ValueError:
            v = self.lo
        return max(self.lo, min(self.hi, v))

    def set(self, v):
        self.var.set(str(max(self.lo, min(self.hi, v))))

    def bump(self, d):
        self.set(self.get() + d * self.step)


class ConfirmVote(ctk.CTkToplevel):
    """'Did you vote?' pop-up with an adjustable time until the next vote."""

    def __init__(self, app, site, on_done):
        super().__init__(app)
        self.app, self.site, self.on_done = app, site, on_done
        self.title("VoteBuddy - did you vote?")
        set_icon(self)
        self.resizable(False, False)
        self.attributes("-topmost", True)  # stay visible over the browser

        ctk.CTkLabel(self, text="Did you vote on", text_color=MUTED,
                     font=ctk.CTkFont(size=13)).pack(padx=24, pady=(18, 0))
        ctk.CTkLabel(self, text=site["name"], font=ctk.CTkFont(size=19, weight="bold"),
                     wraplength=320).pack(padx=24, pady=(0, 12))

        ctk.CTkLabel(self, text="Next vote in:", font=ctk.CTkFont(size=13)).pack()
        total = int(round(site["hours"] * 60))
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(pady=(6, 4), padx=20)
        self.h = Stepper(row, total // 60, 0, 168, "hrs")
        self.h.pack(side="left", padx=(0, 10))
        self.m = Stepper(row, total % 60, 0, 59, "min", step=5)
        self.m.pack(side="left")

        chips = ctk.CTkFrame(self, fg_color="transparent")
        chips.pack(pady=(2, 6))
        for label, hrs in (("6h", 6), ("12h", 12), ("24h", 24)):
            bubble(chips, label, lambda hrs=hrs: (self.h.set(hrs), self.m.set(0)),
                   kind="soft", width=56, height=28).pack(side="left", padx=3)

        self.says = ctk.CTkLabel(self, text="Waiting for the site's timer...", text_color=MUTED,
                                 font=ctk.CTkFont(size=12))
        self.says.pack()

        self.remember = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(self, text=f"Always use this time for this site", variable=self.remember,
                        corner_radius=8).pack(pady=(4, 12))

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(padx=18, pady=(0, 18))
        bubble(btns, "Open page again", lambda: app.open_url(site), kind="soft",
               width=130).pack(side="left", padx=4)
        bubble(btns, "Not yet", lambda: self.finish(False), kind="soft",
               width=90).pack(side="left", padx=4)
        bubble(btns, "I voted ✓", lambda: self.finish(True), kind="go",
               width=110).pack(side="left", padx=4)

        self.protocol("WM_DELETE_WINDOW", lambda: self.finish(False))
        self.bind("<Return>", lambda e: self.finish(True))
        self.bind("<Escape>", lambda e: self.finish(False))
        bring_up(self)
        # CTk re-positions new windows shortly after creation, so move it after that
        for ms in (60, 250, 500):
            self.after(ms, self.place_corner)

    def site_says(self, seconds):
        """The vote site reported its own timer: put it in the boxes."""
        mins = int(-(-seconds // 60))  # round up so we never come back early
        self.h.set(mins // 60)
        self.m.set(mins % 60)
        self.says.configure(text=f"✓ The site says: {mins // 60}h {mins % 60:02d}m",
                            text_color=GREEN)

    def place_corner(self):
        # bottom-right of the screen so it doesn't cover the vote page
        if not self.winfo_exists():
            return
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = self.winfo_screenwidth() - w - 30
        y = self.winfo_screenheight() - h - 90
        tk.Wm.wm_geometry(self, f"+{max(0, x)}+{max(0, y)}")

    def finish(self, voted):
        seconds = (self.h.get() * 60 + self.m.get()) * 60
        if voted and seconds <= 0:
            Popup(self, "Set a time above 0 for the next vote.")
            return
        self.destroy()
        self.on_done(self.site, voted, seconds, self.remember.get())


class SiteDialog(ctk.CTkToplevel):
    def __init__(self, master, site=None):
        super().__init__(master)
        self.title("Edit vote link" if site else "Add vote link")
        self.resizable(False, False)
        self.result = None
        site = site or {"name": "", "url": "", "hours": 24}

        pad = {"padx": 20, "pady": (10, 2), "anchor": "w"}
        ctk.CTkLabel(self, text="Name (e.g. PlanetMinecraft)").pack(**pad)
        self.name = ctk.CTkEntry(self, width=380, corner_radius=RADIUS)
        self.name.insert(0, site["name"])
        self.name.pack(padx=20)

        ctk.CTkLabel(self, text="Vote link (https://...)").pack(**pad)
        self.url = ctk.CTkEntry(self, width=380, corner_radius=RADIUS)
        self.url.insert(0, site["url"])
        self.url.pack(padx=20)

        ctk.CTkLabel(self, text="Vote every how many hours? (most sites = 24, some = 12)").pack(**pad)
        self.hours = ctk.CTkEntry(self, width=90, corner_radius=RADIUS, justify="center")
        self.hours.insert(0, f"{site['hours']:g}")
        self.hours.pack(padx=20, anchor="w")

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(padx=20, pady=18, anchor="e")
        bubble(btns, "Cancel", self.destroy, kind="soft", width=90).pack(side="left", padx=4)
        bubble(btns, "Save", self.ok, width=90).pack(side="left")

        self.bind("<Return>", lambda e: self.ok())
        self.transient(master)
        bring_up(self)
        self.after(150, self.grab_set)
        self.after(200, self.name.focus)
        self.wait_window()

    def ok(self):
        name = self.name.get().strip()
        url = self.url.get().strip()
        try:
            hours = float(self.hours.get())
            if hours <= 0:
                raise ValueError
        except ValueError:
            Popup(self, "Hours must be a number above 0.")
            return
        if not url:
            Popup(self, "Put in the vote link.")
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        self.result = {"name": name or url, "url": url, "hours": hours}
        self.destroy()


class PhoneDialog(ctk.CTkToplevel):
    """Set up phone notifications through the free ntfy app (iPhone + Android)."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Phone alerts")
        set_icon(self)
        self.resizable(False, False)
        data = app.data
        if not data.get("phone_topic"):
            data["phone_topic"] = new_topic()
            save(data)

        ctk.CTkLabel(self, text="Get a ping on your phone when votes are ready",
                     font=ctk.CTkFont(size=17, weight="bold")).pack(padx=24, pady=(20, 8), anchor="w")
        steps = ("1.  Install the free \"ntfy\" app on your phone\n"
                 "      (App Store on iPhone, Google Play on Android).\n"
                 "2.  Open it, tap  +  (Subscribe to topic) and type the name below.\n"
                 "      Leave \"Use another server\" off. Allow notifications when asked.\n"
                 "3.  Click \"Send test\" here. Your phone should ping.")
        ctk.CTkLabel(self, text=steps, justify="left",
                     font=ctk.CTkFont(size=13)).pack(padx=24, anchor="w")

        ctk.CTkLabel(self, text="Your topic name (keep it private, like a password):",
                     text_color=MUTED).pack(padx=24, pady=(14, 2), anchor="w")
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(padx=24, anchor="w")
        self.topic = tk.StringVar(value=data["phone_topic"])
        ctk.CTkEntry(row, textvariable=self.topic, width=260, corner_radius=RADIUS,
                     font=ctk.CTkFont(family="Consolas", size=15, weight="bold")).pack(side="left")
        bubble(row, "Copy", self.copy, kind="soft", width=70).pack(side="left", padx=6)
        bubble(row, "New name", self.regen, kind="soft", width=90).pack(side="left")

        self.on = tk.BooleanVar(value=data.get("phone_on", False))
        ctk.CTkSwitch(self, text="Send alerts to my phone when a vote is ready",
                      variable=self.on, onvalue=True, offvalue=False).pack(padx=24, pady=(16, 4), anchor="w")
        ctk.CTkLabel(self, text="Alerts are sent while VoteBuddy is running on this PC "
                                "(tip: tick \"Start with Windows\").",
                     text_color=MUTED, font=ctk.CTkFont(size=12)).pack(padx=24, anchor="w")

        self.result = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=13))
        self.result.pack(padx=24, pady=(10, 0), anchor="w")

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(padx=20, pady=(8, 18), anchor="e")
        bubble(btns, "Send test", self.test, kind="soft", width=100).pack(side="left", padx=4)
        bubble(btns, "Save", self.ok, kind="go", width=90).pack(side="left", padx=4)

        self.transient(app)
        bring_up(self)
        self.after(150, self.grab_set)

    def clean(self):
        t = "".join(c for c in self.topic.get().strip() if c.isalnum() or c in "-_")[:64]
        self.topic.set(t)
        return t

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.clean())
        self.result.configure(text="Copied.", text_color=MUTED)

    def regen(self):
        self.topic.set(new_topic())
        self.result.configure(text="New name made. Subscribe to it on your phone, then Save.",
                              text_color=MUTED)

    def test(self):
        topic = self.clean()
        if len(topic) < 8:
            self.result.configure(text="Topic name is too short.", text_color="#ef4444")
            return
        self.result.configure(text="Sending...", text_color=MUTED)
        box = {}
        send_phone(topic, "VoteBuddy test 🎉", "Phone alerts work! You'll get a ping like this "
                   "when a vote is ready.", done=lambda err: box.setdefault("err", err or ""))

        def wait(n=0):
            if not self.winfo_exists():
                return
            if "err" not in box:
                if n < 40:
                    self.after(250, lambda: wait(n + 1))
                return
            if box["err"]:
                self.result.configure(text=f"Couldn't send: {box['err'][:80]}", text_color="#ef4444")
            else:
                self.result.configure(text="✓ Sent! Check your phone.", text_color=GREEN)
        wait()

    def ok(self):
        topic = self.clean()
        if self.on.get() and len(topic) < 8:
            self.result.configure(text="Topic name is too short.", text_color="#ef4444")
            return
        self.app.data["phone_topic"] = topic
        self.app.data["phone_on"] = self.on.get()
        save(self.app.data)
        self.app.flash("Phone alerts " + ("on." if self.on.get() else "off."), 5)
        self.destroy()


class App(ctk.CTk):
    def __init__(self):
        self.data = load()
        ctk.set_appearance_mode(self.data["theme"])
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.title("VoteBuddy - Minecraft vote helper")
        set_icon(self)
        self.geometry("1040x540")
        self.minsize(760, 400)
        self.notified = set()      # sites we've already alerted for this cycle
        self.confirm_queue = []    # sites waiting for their "did you vote?" pop-up
        self.confirm_open = False
        self.confirm_win = None
        self.pending_says = {}     # site id -> (seconds, when) for pop-ups not shown yet
        self.msg, self.msg_until = "", 0
        self.listening = start_report_server()

        # ---- top bar ----
        top = ctk.CTkFrame(self, corner_radius=RADIUS)
        top.pack(fill="x", padx=12, pady=(12, 6))
        ctk.CTkLabel(top, text="Minecraft username").pack(side="left", padx=(14, 6), pady=10)
        self.user_var = tk.StringVar(value=self.data["username"])
        ctk.CTkEntry(top, textvariable=self.user_var, width=160, corner_radius=RADIUS,
                     placeholder_text="Steve").pack(side="left", padx=(0, 12))
        self.user_var.trace_add("write", lambda *a: self._set("username", self.user_var.get().strip()))

        self.auto_var = tk.BooleanVar(value=self.data["auto_open"])
        self.fill_var = tk.BooleanVar(value=self.data["autofill"])
        self.submit_var = tk.BooleanVar(value=self.data["autosubmit"])
        self.startup_var = tk.BooleanVar(value=os.path.exists(STARTUP_FILE))
        self.dark_var = tk.BooleanVar(value=self.data["theme"] == "dark")
        for text, var, cmd in (
            ("Auto-open", self.auto_var, lambda: self._set("auto_open", self.auto_var.get())),
            ("Autofill name", self.fill_var, lambda: self._set("autofill", self.fill_var.get())),
            ("Auto-submit if no captcha", self.submit_var,
             lambda: self._set("autosubmit", self.submit_var.get())),
            ("Start with Windows", self.startup_var, self.toggle_startup),
        ):
            ctk.CTkCheckBox(top, text=text, variable=var, command=cmd, corner_radius=8,
                            onvalue=True, offvalue=False).pack(side="left", padx=6)
        ctk.CTkSwitch(top, text="Dark mode", variable=self.dark_var, command=self.toggle_theme,
                      onvalue=True, offvalue=False).pack(side="right", padx=14)

        # bottom rows are packed first so the list can never squash them
        self.status = ctk.CTkLabel(self, text="", text_color=MUTED, anchor="w")
        self.status.pack(side="bottom", fill="x", padx=18, pady=(2, 10))
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(side="bottom", fill="x", padx=12, pady=(6, 4))
        bubble(bar, "+ Add link", self.add, width=110).pack(side="left")
        bubble(bar, "Edit", self.edit, kind="soft", width=80).pack(side="left", padx=6)
        bubble(bar, "Remove", self.remove, kind="soft", width=90).pack(side="left")
        bubble(bar, "Reset timer", self.reset, kind="soft", width=110).pack(side="left", padx=6)
        bubble(bar, "Check timer", self.check_timers, kind="soft", width=110).pack(side="left")
        bubble(bar, "Phone alerts", self.phone_setup, kind="soft", width=120).pack(side="left", padx=6)
        bubble(bar, "Vote ALL ready", self.vote_all_ready, kind="go", width=150).pack(side="right")
        bubble(bar, "Vote selected", self.vote_selected, width=130).pack(side="right", padx=6)

        # ---- site list ----
        box = ctk.CTkFrame(self, corner_radius=RADIUS)
        box.pack(fill="both", expand=True, padx=12, pady=6)
        cols = ("name", "every", "last", "next")
        self.tree = ttk.Treeview(box, columns=cols, show="headings", selectmode="extended",
                                 style="VB.Treeview")
        for c, label, w in (("name", "Site", 300), ("every", "Every", 80),
                            ("last", "Last voted", 170), ("next", "Next vote", 120)):
            self.tree.heading(c, text=label)
            self.tree.column(c, width=w, anchor="w" if c == "name" else "center")
        sb = ctk.CTkScrollbar(box, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(0, 6), pady=10)
        self.tree.pack(fill="both", expand=True, padx=(12, 0), pady=12)
        self.tree.bind("<Double-1>", lambda e: self.vote_selected())
        self.style_tree()

        self.refresh()
        self.after(1000, self.tick)

    # ---- theme ----
    def toggle_theme(self):
        mode = "dark" if self.dark_var.get() else "light"
        self._set("theme", mode)
        ctk.set_appearance_mode(mode)
        self.style_tree()

    def style_tree(self):
        dark = ctk.get_appearance_mode() == "Dark"
        bg, fg = ("#24242b", "#e8e8ee") if dark else ("#ffffff", "#1f1f24")
        head = "#2f2f38" if dark else "#ececf1"
        sel = "#3b5bdb" if dark else "#c7d2fe"
        sel_fg = "#ffffff" if dark else "#1f1f24"
        try:
            scale = ctk.ScalingTracker.get_widget_scaling(self)
        except Exception:
            scale = 1.0
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure("VB.Treeview", background=bg, fieldbackground=bg, foreground=fg,
                     rowheight=int(32 * scale), borderwidth=0, font=("Segoe UI", 11))
        st.configure("VB.Treeview.Heading", background=head, foreground=fg, relief="flat",
                     borderwidth=0, font=("Segoe UI", 11, "bold"), padding=(6, 6))
        st.map("VB.Treeview.Heading", background=[("active", head)])
        st.map("VB.Treeview", background=[("selected", sel)], foreground=[("selected", sel_fg)])
        st.layout("VB.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])  # no border
        self.tree.tag_configure("ready", foreground="#4ade80" if dark else "#15803d")

    # ---- data helpers ----
    def _set(self, key, value):
        self.data[key] = value
        save(self.data)

    def sites(self):
        return self.data["sites"]

    def due_in(self, site):
        nxt = site.get("next_at")
        if not nxt:
            last = site.get("last", 0)
            nxt = last + site["hours"] * 3600 if last else 0
        return nxt - time.time()

    def selected_indexes(self):
        return [int(i) for i in self.tree.selection()]

    # ---- UI ----
    def refresh(self):
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for i, s in enumerate(self.sites()):
            left = self.due_in(s)
            last = time.strftime("%b %d  %I:%M %p", time.localtime(s["last"])) if s.get("last") else "never"
            self.tree.insert("", "end", iid=str(i),
                             values=(s["name"], f"{s['hours']:g}h", last, fmt_left(left)),
                             tags=("ready",) if left <= 0 else ())
        for i in sel:
            if self.tree.exists(i):
                self.tree.selection_add(i)
        if time.time() < self.msg_until:
            self.status.configure(text=self.msg)
            return
        ready = sum(1 for s in self.sites() if self.due_in(s) <= 0)
        self.status.configure(text=f"{len(self.sites())} sites  •  {ready} ready to vote"
                              + ("" if self.data["username"] else "  •  set your username up top!"))

    def tick(self):
        newly_due = []
        for i, s in enumerate(self.sites()):
            if self.due_in(s) <= 0:
                if s["url"] not in self.notified:
                    self.notified.add(s["url"])
                    newly_due.append(i)
            else:
                self.notified.discard(s["url"])
        if newly_due:
            self.phone_alert(newly_due)
            if self.auto_var.get():
                self.open_votes(newly_due)
            else:
                self.alert(len(newly_due))
        while not REPORTS.empty():
            self.apply_site_timer(*REPORTS.get_nowait())
        self.refresh()
        self.after(1000, self.tick)

    def apply_site_timer(self, site_id, seconds):
        site = next((s for s in self.sites() if s.get("id") == site_id), None)
        if not site:
            return
        win = self.confirm_win
        if win is not None and win.site is site and win.winfo_exists():
            win.site_says(seconds)  # you confirm it in the pop-up
            return
        if any(s is site for s in self.confirm_queue):
            self.pending_says[site_id] = (seconds, time.time())  # shown when its pop-up opens
            return
        site["next_at"] = time.time() + seconds
        save(self.data)
        self.notified.discard(site["url"])
        self.flash(f"{site['name']} says next vote in {fmt_left(seconds)} - timer updated.", 8)

    def alert(self, n):
        self.bell()
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(1500, lambda: self.attributes("-topmost", False))
        self.title(f"({n} ready) VoteBuddy - Minecraft vote helper")

    def phone_alert(self, indexes):
        topic = self.data.get("phone_topic")
        if not (self.data.get("phone_on") and topic):
            return
        sites = [self.sites()[i] for i in indexes]
        n = len(sites)
        title = f"{sites[0]['name']} is ready to vote!" if n == 1 else f"{n} votes are ready!"
        names = ", ".join(s["name"] for s in sites[:6]) + (f" +{n - 6} more" if n > 6 else "")
        msg = names + "\nTap to open the vote page." if n == 1 else names + "\nTap a button to open a vote page."
        send_phone(topic, title, msg, [(s["name"], s["url"]) for s in sites],
                   done=lambda err: err and self.flash(f"Couldn't send phone alert: {err}", 15))

    def phone_setup(self):
        PhoneDialog(self)

    def flash(self, msg, seconds=12):
        self.msg, self.msg_until = msg, time.time() + seconds

    # ---- voting ----
    def open_url(self, site, check_only=False):
        url = site["url"]
        user = self.data["username"]
        if user:
            self.clipboard_clear()
            self.clipboard_append(user)
        if user and self.fill_var.get():
            # the VoteBuddy Autofill extension reads this tag; it stays in
            # the browser (text after # is never sent to the website)
            url += ("&" if "#" in url else "#") + "vbuser=" + quote(user, safe="")
            url += "&vbsite=" + site["id"]  # so the site's timer comes back to this row
            if self.submit_var.get() and not check_only:
                url += "&vbsubmit=1"  # extension clicks Vote only if there's no captcha
        open_in_browser(url)  # Chrome if installed, else the default browser

    def open_votes(self, indexes):
        if not indexes:
            return
        for n, i in enumerate(indexes):
            site = self.sites()[i]
            self.open_url(site)
            self.queue_confirm(site)
            if n < len(indexes) - 1:
                time.sleep(0.4)  # keeps the browser from dropping tabs
        self.title("VoteBuddy - Minecraft vote helper")
        self.flash(f"Opened {len(indexes)} vote page(s). Confirm each one in the pop-up when you've voted.")
        self.refresh()

    def queue_confirm(self, site):
        if any(s is site for s in self.confirm_queue):
            return
        self.confirm_queue.append(site)
        if not self.confirm_open:
            self.next_confirm()

    def next_confirm(self):
        while self.confirm_queue:
            site = self.confirm_queue[0]
            if any(s is site for s in self.sites()):  # not removed meanwhile
                self.confirm_open = True
                self.confirm_win = ConfirmVote(self, site, self.confirm_done)
                said = self.pending_says.pop(site.get("id"), None)
                if said:
                    secs = said[0] - (time.time() - said[1])
                    if secs > 60:
                        self.confirm_win.site_says(secs)
                return
            self.confirm_queue.pop(0)
        self.confirm_open = False

    def confirm_done(self, site, voted, seconds, remember):
        self.confirm_queue.pop(0)
        self.confirm_win = None
        if voted:
            now = time.time()
            site["last"] = now
            site["next_at"] = now + seconds
            if remember:
                site["hours"] = round(seconds / 3600, 2)
            save(self.data)
            self.flash(f"Logged vote on {site['name']}. Next one in {fmt_left(seconds)}.", 6)
        else:
            self.flash(f"{site['name']} left as READY. Vote whenever you're ready.", 6)
        self.refresh()
        self.next_confirm()

    def check_timers(self):
        idx = self.selected_indexes()
        if not idx:
            Popup(self, "Click a site (or several) in the list first.")
            return
        if not (self.data["username"] and self.fill_var.get()):
            Popup(self, "Checking timers needs your username set and 'Autofill name' ticked.")
            return
        for n, i in enumerate(idx):
            self.open_url(self.sites()[i], check_only=True)
            if n < len(idx) - 1:
                time.sleep(0.4)
        self.flash("Opened for a timer check (no voting). If a site shows a "
                   "'vote again in...' timer, the list updates by itself.", 15)

    def vote_selected(self):
        idx = self.selected_indexes()
        if not idx:
            Popup(self, "Click a site in the list first.")
            return
        self.open_votes(idx)

    def vote_all_ready(self):
        idx = [i for i, s in enumerate(self.sites()) if self.due_in(s) <= 0]
        if not idx:
            Popup(self, "Nothing's ready yet. Check the 'Next vote' column.")
            return
        self.open_votes(idx)

    # ---- list editing ----
    def add(self):
        d = SiteDialog(self)
        if d.result:
            d.result["last"] = 0
            d.result["id"] = uuid.uuid4().hex[:10]
            self.sites().append(d.result)
            save(self.data)
            self.refresh()

    def edit(self):
        idx = self.selected_indexes()
        if len(idx) != 1:
            Popup(self, "Select one site to edit.")
            return
        s = self.sites()[idx[0]]
        d = SiteDialog(self, s)
        if d.result:
            s.update(d.result)
            save(self.data)
            self.refresh()

    def remove(self):
        idx = self.selected_indexes()
        if not idx:
            return
        if Popup(self, f"Remove {len(idx)} site(s)?", ("Cancel", "Remove")).choice != "Remove":
            return
        for i in sorted(idx, reverse=True):
            del self.sites()[i]
        save(self.data)
        self.refresh()

    def reset(self):
        for i in self.selected_indexes():
            self.sites()[i]["last"] = 0
            self.sites()[i]["next_at"] = 0
        save(self.data)
        self.refresh()

    def toggle_startup(self):
        try:
            if self.startup_var.get():
                if FROZEN:
                    cmd = f'@start "" "{sys.executable}"\n'
                else:
                    pyw = sys.executable.replace("python.exe", "pythonw.exe")
                    cmd = f'@start "" "{pyw}" "{os.path.abspath(__file__)}"\n'
                with open(STARTUP_FILE, "w") as f:
                    f.write(cmd)
            elif os.path.exists(STARTUP_FILE):
                os.remove(STARTUP_FILE)
        except OSError as e:
            Popup(self, f"Couldn't change startup setting:\n{e}")
            self.startup_var.set(os.path.exists(STARTUP_FILE))


if __name__ == "__main__":
    App().mainloop()
