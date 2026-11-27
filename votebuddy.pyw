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
def package_family_name():
    """Our Microsoft Store package name when installed from the Store, else None."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        n = ctypes.c_uint32(0)
        k32 = ctypes.windll.kernel32
        if k32.GetCurrentPackageFamilyName(ctypes.byref(n), None) == 15700:  # not packaged
            return None
        buf = ctypes.create_unicode_buffer(n.value)
        if k32.GetCurrentPackageFamilyName(ctypes.byref(n), buf) != 0:
            return None
        return buf.value or None
    except Exception:
        return None


STORE_PFN = package_family_name()  # set = Store install: the Store handles updates and startup
# The browser has to be able to see the extension folder. A Store app's own folder is
# hidden and read-only, so the Store version copies the extension to the user's home folder.
EXT_DIR = (os.path.join(os.path.expanduser("~"), "VoteBuddy Extension") if STORE_PFN
           else os.path.join(APP_DIR, "extension"))


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

# ---- server ad banner + credits (change these to advertise a different server) ----
AD_TEXT = "Join us here at Chebish.net"
AD_SUBTEXT = "Minecraft server  •  click to copy the IP"
AD_IP = "chebishmc.net"   # clicking the banner copies this server IP
AD_GIF = "banner.gif"   # 468x60 animated banner shipped with the app; text above is the fallback
CREDITS = "Made by MonkeySoto"
AD_PROMO = "Want to Advertise your Minecraft server?\nContact monkeysoto on Discord"

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
    """Listen in the background. Keeps retrying for a bit, because right after an
    update the old copy of VoteBuddy may still be closing and holding the port."""
    def run():
        for _ in range(30):
            try:
                srv = ThreadingHTTPServer(("127.0.0.1", PORT), ReportHandler)
            except OSError:
                time.sleep(0.5)  # port busy (old copy closing, or VoteBuddy open twice)
                continue
            srv.serve_forever()
            return
    threading.Thread(target=run, daemon=True).start()
    return True


# ---- updates from GitHub ----
APP_VERSION = "1.4"
GITHUB_REPO = "monkeysoto/VoteBuddy"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"


def version_tuple(v):
    parts = []
    for p in str(v).lstrip("vV").split("."):
        digits = "".join(c for c in p if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def http_get(url, accept="application/json", progress=None):
    """GET a URL with the same certificate fallbacks as phone alerts."""
    import urllib.request
    err = None
    for ctx in ssl_contexts():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "VoteBuddy", "Accept": accept})
            with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                total = int(r.headers.get("Content-Length") or 0)
                chunks, got = [], 0
                while True:
                    block = r.read(256 * 1024)
                    if not block:
                        break
                    chunks.append(block)
                    got += len(block)
                    if progress and total:
                        progress(got / total)
                return b"".join(chunks)
        except Exception as e:
            err = e
    raise err


def latest_release():
    """-> (version, notes, zip_url) of the newest GitHub release."""
    test_feed = os.environ.get("VOTEBUDDY_TEST_FEED")  # only for testing updates locally
    feed = test_feed or f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    if test_feed:
        import urllib.request
        with urllib.request.urlopen(test_feed, timeout=10) as r:
            info = json.loads(r.read())
    else:
        info = json.loads(http_get(feed, "application/vnd.github+json"))
    zip_url = ""
    prefix = "http://127.0.0.1:" if test_feed else f"https://github.com/{GITHUB_REPO}/releases/download/"
    for a in info.get("assets", []):
        url = a.get("browser_download_url", "")
        if a.get("name", "").lower().endswith(".zip") and url.startswith(prefix):
            zip_url = url
            break
    return info.get("tag_name", "").lstrip("vV"), info.get("body") or "", zip_url


def extension_version(folder):
    try:
        with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as f:
            return json.load(f).get("version", "")
    except (OSError, ValueError):
        return ""


def install_update(zip_bytes):
    """Put the new VoteBuddy.exe (and extension files) in place of this one.
    Windows can't overwrite a running .exe, but it can rename it, so the old one
    becomes VoteBuddy.old.exe and is deleted the next time VoteBuddy starts.
    Returns True if the browser extension changed."""
    import io
    import zipfile
    exe = sys.executable
    old = os.path.splitext(exe)[0] + ".old.exe"
    ext_dir = os.path.join(APP_DIR, "extension")
    ext_before = extension_version(ext_dir)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        members = {n.replace("\\", "/"): n for n in z.namelist()}
        exe_name = next((n for n in members if n.lower().endswith(".exe") and "/" not in n.strip("/")), None)
        if not exe_name:
            raise ValueError("the download has no VoteBuddy.exe in it")
        new_exe = z.read(members[exe_name])
        if len(new_exe) < 1_000_000:
            raise ValueError("the downloaded VoteBuddy.exe looks broken")
        # extension files + readme next to the app
        for name, real in members.items():
            if name.endswith("/") or name == exe_name:
                continue
            if not (name.startswith("extension/") or name.lower() == "readme.md"):
                continue
            if ".." in name.split("/"):
                continue
            dest = os.path.join(APP_DIR, *name.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(z.read(real))
    tmp = exe + ".new"
    with open(tmp, "wb") as f:
        f.write(new_exe)
    if os.path.exists(old):
        os.remove(old)
    os.replace(exe, old)
    try:
        os.replace(tmp, exe)
    except OSError:
        os.replace(old, exe)  # put the working one back
        raise
    return extension_version(ext_dir) != ext_before


def cleanup_old_exe():
    if FROZEN:
        old = os.path.splitext(sys.executable)[0] + ".old.exe"
        for _ in range(10):
            try:
                if os.path.exists(old):
                    os.remove(old)
                return
            except OSError:
                time.sleep(0.5)  # old copy still closing


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
    def up():
        if not win.winfo_exists():
            return
        win.lift()
        win.focus_force()
        if win.attributes("-topmost"):
            return  # already always-on-top (the "did you vote?" pop-up)
        win.attributes("-topmost", True)   # briefly on top so it can't hide behind anything
        win.after(400, lambda: win.winfo_exists() and win.attributes("-topmost", False))
    win.after(120, up)
    win.after(700, up)  # again, in case the main window was still opening


class Popup(ctk.CTkToplevel):
    """Dark-mode friendly message box. Returns the clicked button's text."""

    def __init__(self, master, text, buttons=("OK",), title="VoteBuddy", wrap=340):
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.choice = None
        ctk.CTkLabel(self, text=text, font=ctk.CTkFont(size=14), wraplength=wrap,
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
        self.title("VoteBuddy - Server Vote helper")
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
        self.startup_var = tk.BooleanVar(value=self.startup_enabled())
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
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(side="bottom", fill="x", padx=18, pady=(2, 10))
        ctk.CTkLabel(foot, text=CREDITS + " ", text_color=MUTED,  # space: italics clip the last letter
                     font=ctk.CTkFont(size=12, slant="italic")).pack(side="right")
        if STORE_PFN:  # the Microsoft Store keeps this version up to date
            self.update_link = ctk.CTkLabel(foot, text=f"v{APP_VERSION}  •  Microsoft Store", text_color=MUTED,
                                            font=ctk.CTkFont(size=12))
        else:
            self.update_link = ctk.CTkLabel(foot, text=f"v{APP_VERSION}  •  Check for updates", text_color=MUTED,
                                            cursor="hand2", font=ctk.CTkFont(size=12, underline=True))
            self.update_link.bind("<Button-1>", lambda e: self.check_updates(manual=True))
        self.update_link.pack(side="right", padx=(0, 16))
        ext_link = ctk.CTkLabel(foot, text="Browser extension", text_color=MUTED, cursor="hand2",
                                font=ctk.CTkFont(size=12, underline=True))
        ext_link.pack(side="right", padx=(0, 16))
        ext_link.bind("<Button-1>", lambda e: self.extension_help())
        self.new_release = None  # (version, notes, zip_url) once we know there's a newer one
        self.status = ctk.CTkLabel(foot, text="", text_color=MUTED, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
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
        ad_row = ctk.CTkFrame(box, fg_color="transparent")
        ad_row.pack(side="bottom", fill="x", padx=12, pady=(0, 12))
        self.make_ad(ad_row).pack(side="right")
        ctk.CTkLabel(ad_row, text=AD_PROMO, text_color=MUTED, justify="left",
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=(4, 12))
        sb = ctk.CTkScrollbar(box, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(0, 6), pady=(10, 6))
        self.tree.pack(fill="both", expand=True, padx=(12, 0), pady=(12, 8))
        self.tree.bind("<Double-1>", lambda e: self.vote_selected())
        self.style_tree()

        self.refresh()
        self.after(1000, self.tick)
        if STORE_PFN:
            self.after(1500, self.sync_store_extension)
            self.bind("<FocusIn>", lambda e: self.startup_var.set(self.startup_enabled()), add="+")
        else:
            threading.Thread(target=cleanup_old_exe, daemon=True).start()
            self.after(1500, self.after_update_note)
            self.after(5000, lambda: self.check_updates(manual=False))

    # ---- browser extension ----
    def sync_store_extension(self):
        """Store version: keep a copy of the extension where the browser can load it."""
        import shutil
        src = os.path.join(APP_DIR, "extension")
        new, old = extension_version(src), extension_version(EXT_DIR)
        if not new or new == old:
            return
        try:
            shutil.copytree(src, EXT_DIR, dirs_exist_ok=True)
        except OSError as e:
            self.flash(f"Couldn't copy the browser extension: {e}", 15)
            return
        if old:
            Popup(self, "The browser extension was updated. Open your browser's extensions page and "
                        "switch VoteBuddy Autofill OFF and back ON so the new version loads.")
        elif not self.data.get("ext_help_shown"):
            self._set("ext_help_shown", True)
            self.extension_help(first=True)

    def extension_help(self, first=False):
        intro = ("Welcome to VoteBuddy!\n\n" if first else "") + (
            "The free VoteBuddy Autofill extension types your username in, clicks Vote when "
            "there's no captcha and reads each site's timer. It takes 30 seconds to add:\n\n"
            "1. In Chrome open  chrome://extensions  (Edge: edge://extensions, Opera: opera://extensions)\n"
            "2. Turn on Developer mode\n"
            "3. Click Load unpacked and pick this folder:\n"
            f"     {EXT_DIR}\n\n"
            "VoteBuddy works without it too - you just paste your name and click Vote yourself.")
        if Popup(self, intro, ("Close", "Open folder"), wrap=560).choice == "Open folder":
            os.makedirs(EXT_DIR, exist_ok=True)
            os.startfile(EXT_DIR)

    # ---- updates ----
    def run_bg(self, work, done):
        """Run work() on a thread, then done(result, error) back on the UI thread."""
        box = {}

        def worker():
            try:
                box["r"] = work()
            except Exception as e:
                box["e"] = e
            box["done"] = True
        threading.Thread(target=worker, daemon=True).start()

        def poll():
            if "done" in box:
                done(box.get("r"), box.get("e"))
            else:
                self.after(150, poll)
        poll()

    def check_updates(self, manual):
        if self.new_release and manual:
            return self.offer_update(*self.new_release)
        if manual:
            self.update_link.configure(text="Checking...")

        def done(res, err):
            self.update_link.configure(text=f"v{APP_VERSION}  •  Check for updates", text_color=MUTED)
            if err or not res or not res[0]:
                if manual:
                    Popup(self, f"Couldn't check for updates right now.\n\n{err or 'No release found.'}")
                return
            if version_tuple(res[0]) > version_tuple(APP_VERSION):
                self.new_release = res
                self.update_link.configure(text=f"⬆ Update to v{res[0]} available - click here",
                                           text_color=GREEN)
                if manual:
                    self.offer_update(*res)
                else:
                    self.flash(f"A new VoteBuddy (v{res[0]}) is out! Click the green link at the bottom to update.", 20)
            elif manual:
                Popup(self, f"You're up to date! (v{APP_VERSION})")
        self.run_bg(latest_release, done)

    def offer_update(self, version, notes, zip_url):
        notes = "\n".join(l for l in notes.splitlines() if l.strip())[:500]
        notes = notes.replace("**", "")
        text = f"VoteBuddy v{version} is out (you have v{APP_VERSION}).\n\n{notes}\n\nUpdate now? VoteBuddy will restart. Your vote list is kept."
        if Popup(self, text, ("Later", "Update now")).choice != "Update now":
            return
        if not FROZEN or not zip_url:
            open_in_browser(RELEASES_PAGE)  # running from source: just show the download page
            return
        win = ctk.CTkToplevel(self)
        win.title("Updating VoteBuddy")
        set_icon(win)
        win.resizable(False, False)
        win.transient(self)
        label = ctk.CTkLabel(win, text=f"Downloading v{version}...", font=ctk.CTkFont(size=14))
        label.pack(padx=28, pady=(22, 8))
        bar = ctk.CTkProgressBar(win, width=300)
        bar.set(0)
        bar.pack(padx=28, pady=(0, 24))
        bring_up(win)
        prog = {"p": 0.0}

        def show_progress():
            if win.winfo_exists() and prog["p"] < 1:
                bar.set(prog["p"])
                win.after(150, show_progress)
        show_progress()

        def work():
            data = http_get(zip_url, "application/octet-stream",
                            progress=lambda p: prog.__setitem__("p", min(p, 0.99)))
            return install_update(data)

        def done(ext_changed, err):
            prog["p"] = 1
            if err:
                win.destroy()
                Popup(self, f"The update didn't work, so nothing was changed.\n\n{err}\n\n"
                            f"You can download it yourself from GitHub instead.", ("OK",))
                return
            bar.set(1)
            label.configure(text="Done! Restarting VoteBuddy...")
            self.data["updated_from"] = APP_VERSION
            self.data["ext_changed"] = bool(ext_changed)
            save(self.data)
            self.after(800, self.restart)
        self.run_bg(work, done)

    def restart(self):
        import subprocess
        env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")  # start as a fresh app
        subprocess.Popen([sys.executable], env=env, close_fds=True)
        self.destroy()

    def after_update_note(self):
        old = self.data.pop("updated_from", None)
        ext = self.data.pop("ext_changed", False)
        if not old:
            return
        save(self.data)
        msg = f"VoteBuddy updated from v{old} to v{APP_VERSION}!"
        if ext:
            msg += ("\n\nThe browser extension was updated too. Open your browser's extensions page "
                    "and switch VoteBuddy Autofill OFF and back ON so the new version loads.")
        Popup(self, msg)

    def make_ad(self, master):
        """Clickable server banner: the animated banner.gif, or a drawn one if it can't load."""
        try:
            from PIL import Image
            gif = Image.open(resource(AD_GIF))
            frames, delays = [], []
            for i in range(getattr(gif, "n_frames", 1)):
                gif.seek(i)
                frames.append(ctk.CTkImage(gif.convert("RGBA"), size=gif.size))
                delays.append(max(20, gif.info.get("duration", 100) or 100))
        except Exception:
            return self.make_drawn_ad(master)

        label = ctk.CTkLabel(master, text="", image=frames[0], cursor="hand2")
        label.bind("<Button-1>", lambda e: self.copy_ip(label))

        def play(i=0):
            if not label.winfo_exists():
                return
            label.configure(image=frames[i])
            label.after(delays[i], play, (i + 1) % len(frames))
        if len(frames) > 1:
            label.after(delays[0], play, 1)
        return label

    def copy_ip(self, banner):
        """Banner click: copy the server IP and show a little bubble next to the banner."""
        self.clipboard_clear()
        self.clipboard_append(AD_IP)
        toast = getattr(self, "ip_toast", None)
        if toast is None or not toast.winfo_exists():
            toast = self.ip_toast = ctk.CTkLabel(
                banner.master, text=f"✓ Server IP copied to your clipboard ({AD_IP})",
                fg_color=GREEN, text_color="#ffffff", corner_radius=12, height=30,
                font=ctk.CTkFont(size=12, weight="bold"))
        toast.place(in_=banner, relx=0, rely=0.5, anchor="e", x=-10)
        toast.lift()
        if getattr(self, "ip_toast_job", None):
            self.after_cancel(self.ip_toast_job)
        self.ip_toast_job = self.after(2500, toast.place_forget)

    def make_drawn_ad(self, master):
        """Drawn server banner (peach bar with a light pill in the middle)."""
        peach, peach_hover = "#f4b886", "#eea66c"
        ad = ctk.CTkFrame(master, width=468, height=64, corner_radius=14, fg_color=peach,
                          cursor="hand2")
        ad.pack_propagate(False)
        pill = ctk.CTkFrame(ad, corner_radius=26, fg_color="#f6e7dc", cursor="hand2")
        pill.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.74, relheight=0.8)
        title = ctk.CTkLabel(pill, text=AD_TEXT, text_color="#b45309", cursor="hand2",
                             font=ctk.CTkFont(size=15, weight="bold"))
        title.place(relx=0.5, rely=0.36, anchor="center")
        sub = ctk.CTkLabel(pill, text=AD_SUBTEXT, text_color="#c2703a", cursor="hand2",
                           font=ctk.CTkFont(size=11))
        sub.place(relx=0.5, rely=0.74, anchor="center")

        def hover(on):
            ad.configure(fg_color=peach_hover if on else peach)
        for w in (ad, pill, title, sub):
            w.bind("<Button-1>", lambda e: self.copy_ip(ad))
            w.bind("<Enter>", lambda e: hover(True))
            w.bind("<Leave>", lambda e: hover(False))
        return ad

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
        self.title(f"({n} ready) VoteBuddy - Server Vote helper")

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
        self.title("VoteBuddy - Server Vote helper")
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

    def startup_enabled(self):
        if not STORE_PFN:
            return os.path.exists(STARTUP_FILE)
        try:  # Windows keeps the Store startup task's on/off state here (2 = on, 4 = on by policy)
            import winreg
            key = (r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion"
                   rf"\AppModel\SystemAppData\{STORE_PFN}\VoteBuddyStartup")
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                return winreg.QueryValueEx(k, "State")[0] in (2, 4)
        except OSError:
            return False

    def toggle_startup(self):
        if STORE_PFN:
            # Store apps can't add themselves to startup; Windows' Startup apps page has the switch
            self.startup_var.set(self.startup_enabled())
            Popup(self, "Windows' Startup apps settings will open.\n\n"
                        "Find VoteBuddy in the list and switch it On (or Off).")
            os.startfile("ms-settings:startupapps")
            return
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
