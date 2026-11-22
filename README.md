# VoteBuddy

A little Windows app that keeps track of your Minecraft server vote links so you never miss a vote reward.

- Add your vote links and how often each one can be voted on
- Countdown for every site, with a beep/pop-up when votes are ready
- Opens vote pages in your normal browser with your username **already filled in**
- Clicks **Vote** for you on sites that **don't** have a captcha
- Reads the site's own "you can vote again in…" timer so your countdowns are exact
- "Did you vote?" pop-up after each site, where you can adjust the time until the next vote
- Dark mode, start-with-Windows option

> VoteBuddy never solves or clicks captchas / "verify you're human" boxes. On sites that have one, it fills your name and you tick the box and click Vote yourself. Most servers ban automated captcha solving, so this keeps your account safe.

## Download & setup

1. Go to the **Releases** page (right side of this repo) and download `VoteBuddy.zip`.
2. Unzip it somewhere permanent, like your Documents folder.
3. Double-click **VoteBuddy.exe**.
   - Windows may say "Windows protected your PC" because the app isn't signed. Click **More info → Run anyway**.
4. Install the browser extension (needed for autofill, auto-vote and timers). Follow `extension/HOW TO INSTALL.txt`. It takes about 30 seconds in Opera / Opera GX, Chrome, Edge or Brave.
5. In VoteBuddy, type your Minecraft username at the top, click **+ Add link**, and paste your server's vote links.

Your list is saved in `%APPDATA%\VoteBuddy\votes.json`, so it's kept when you update or move the app.

## Using it

| Button | What it does |
| --- | --- |
| **Vote ALL ready** | Opens every site that's ready. |
| **Vote selected** / double-click | Opens just that site. |
| **Check timer** | Opens the site only to read its "vote again in…" timer (no voting). |
| **Reset timer** | Marks a site as ready now. |

| Option | What it does |
| --- | --- |
| **Auto-open** | Opens vote pages by itself the moment they're ready. |
| **Autofill name** | Lets the extension type your username in. |
| **Auto-submit if no captcha** | Lets the extension click Vote when there's no captcha. |

## Building it yourself

Needs Python 3.12+ on Windows.

```bat
pip install -r requirements.txt pyinstaller
build.bat
```

The finished app is in `release\VoteBuddy.zip`. You can also run it without building: `pythonw votebuddy.pyw`.

## How it works (for the curious)

- `votebuddy.pyw` is the app (Python + CustomTkinter).
- `extension/` is a small browser extension. VoteBuddy opens links with a tag after the `#` (for example `#vbuser=Steve`). That part of a link stays in your browser and is never sent to the website. The extension only acts on tabs with that tag.
- The extension sends site timers to the app at `http://127.0.0.1:47615`. That address only works on your own PC, and the app only accepts it from a browser extension, not from websites.
