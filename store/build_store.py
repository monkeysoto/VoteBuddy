"""Builds the Microsoft Store package (release/VoteBuddy-<version>.msix).

    python store\\build_store.py           Store build (needs store_config.json filled in)
    python store\\build_store.py --test    local test build with a made-up identity

Needs: pip install -r requirements.txt pyinstaller pillow, and the Windows SDK (makeappx.exe).
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(ROOT, "build", "store")
LAYOUT = os.path.join(BUILD, "layout")
TEST = "--test" in sys.argv


def app_version():
    src = open(os.path.join(ROOT, "votebuddy.pyw"), encoding="utf-8-sig").read()
    v = re.search(r'^APP_VERSION = "([\d.]+)"', src, re.M).group(1)
    parts = (v.split(".") + ["0", "0", "0"])[:3]
    return ".".join(parts) + ".0"  # Store versions are a.b.c.0


def config():
    if TEST:
        return {"identity_name": "VoteBuddy.LocalTest", "publisher": "CN=VoteBuddyLocalTest",
                "publisher_display_name": "VoteBuddy test build", "display_name": "VoteBuddy (test)"}
    cfg = json.load(open(os.path.join(HERE, "store_config.json"), encoding="utf-8"))
    if any("FILL_ME_IN" in str(v) for v in cfg.values()):
        sys.exit("Fill in store/store_config.json from Partner Center first (or use --test).")
    return cfg


def find_makeappx():
    hits = sorted(glob.glob(r"C:\Program Files (x86)\Windows Kits\10\bin\*\x64\makeappx.exe"))
    if not hits:
        sys.exit("makeappx.exe not found - install the Windows SDK.")
    return hits[-1]


def make_logos(dest):
    os.makedirs(dest, exist_ok=True)
    ico = Image.open(os.path.join(ROOT, "votebuddy.ico"))
    ico.size = max(ico.info["sizes"])
    icon = ico.convert("RGBA")

    def square(px, name, pad=0.0):
        canvas = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        inner = int(px * (1 - 2 * pad))
        canvas.paste(icon.resize((inner, inner), Image.LANCZOS), ((px - inner) // 2,) * 2)
        canvas.save(os.path.join(dest, name))

    square(44, "Square44x44Logo.png")
    square(150, "Square150x150Logo.png", pad=0.16)
    square(50, "StoreLogo.png")
    wide = Image.new("RGBA", (310, 150), (0, 0, 0, 0))
    wide.paste(icon.resize((100, 100), Image.LANCZOS), (105, 25))
    wide.save(os.path.join(dest, "Wide310x150Logo.png"))


def main():
    cfg, version = config(), app_version()
    shutil.rmtree(BUILD, ignore_errors=True)
    # one-folder build: starts faster than one-file and is friendlier to Store virus scans
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed",
                    "--name", "VoteBuddy", "--icon", os.path.join(ROOT, "votebuddy.ico"),
                    "--add-data", os.path.join(ROOT, "votebuddy.ico") + ";.",
                    "--add-data", os.path.join(ROOT, "banner.gif") + ";.",
                    "--collect-data", "customtkinter",
                    "--distpath", os.path.join(BUILD, "dist"), "--workpath", os.path.join(BUILD, "work"),
                    "--specpath", os.path.join(BUILD, "work"),
                    os.path.join(ROOT, "votebuddy.pyw")], check=True)
    shutil.copytree(os.path.join(BUILD, "dist", "VoteBuddy"), LAYOUT)
    shutil.copytree(os.path.join(ROOT, "extension"), os.path.join(LAYOUT, "extension"))
    make_logos(os.path.join(LAYOUT, "Assets"))

    manifest = open(os.path.join(HERE, "AppxManifest.template.xml"), encoding="utf-8").read()
    for key, val in {"IDENTITY_NAME": cfg["identity_name"], "PUBLISHER": cfg["publisher"],
                     "PUBLISHER_DISPLAY_NAME": cfg["publisher_display_name"],
                     "DISPLAY_NAME": cfg.get("display_name", "VoteBuddy"), "VERSION": version}.items():
        manifest = manifest.replace("{{" + key + "}}", val.replace("&", "&amp;").replace('"', "&quot;"))
    open(os.path.join(LAYOUT, "AppxManifest.xml"), "w", encoding="utf-8").write(manifest)

    out = os.path.join(ROOT, "release", f"VoteBuddy-{version}{'-test' if TEST else ''}.msix")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    subprocess.run([find_makeappx(), "pack", "/d", LAYOUT, "/p", out, "/o"], check=True)
    print(f"\nDone: {out}\nLoose files (for local testing): {LAYOUT}")


if __name__ == "__main__":
    main()
