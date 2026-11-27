# VoteBuddy

A little Windows app that keeps track of your Minecraft server vote links so you never miss a vote reward.

- Add your vote links and how often each one can be voted on
- Countdown for every site, with a beep/pop-up when votes are ready
- Opens vote pages in your normal browser with your username **already filled in**
- Clicks **Vote** for you on sites that **don't** have a captcha
- Reads the site's own "you can vote again in…" timer so your countdowns are exact
- "Did you vote?" pop-up after each site, where you can adjust the time until the next vote
- **Phone alerts** on iPhone and Android: your phone pings when a vote is ready, and tapping the alert opens the vote page
- **Updates itself**: checks GitHub on start, and "Check for updates" at the bottom installs a new version in one click
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

| **Phone alerts** | Sets up phone notifications (see below). |

| Option | What it does |
| --- | --- |
| **Auto-open** | Opens vote pages by itself the moment they're ready. |
| **Autofill name** | Lets the extension type your username in. |
| **Auto-submit if no captcha** | Lets the extension click Vote when there's no captcha. |

## Phone alerts (iPhone & Android)

VoteBuddy can ping your phone when a vote is ready. It uses [ntfy](https://ntfy.sh), a free notification app. You don't need an account.

1. Install **ntfy** on your phone from the App Store (iPhone) or Google Play (Android).
2. In VoteBuddy, click **Phone alerts**. It shows a private topic name such as `votebuddy-k3m9q2xh7p`.
3. In the ntfy app, tap **+**, type that topic name and subscribe. Allow notifications when it asks.
4. Back in VoteBuddy, click **Send test**. Your phone should ping. Then turn on the switch and click **Save**.

When you get an alert, tap it (or one of its buttons) to open the vote page on your phone.

Good to know:
- Alerts are sent by VoteBuddy on your PC, so the PC has to be on with VoteBuddy running. Tick **Start with Windows**.
- Treat the topic name like a password. Anyone who knows it can see your alerts. If it leaks, click **New name**.
- If you vote on your phone, VoteBuddy on the PC doesn't know about it. Use **Vote selected** → **I voted** later, or let **Check timer** read the site's timer.

## Updates

From v1.3 on, VoteBuddy checks for a new version each time it starts. When one is out, a green **Update to vX available** link appears at the bottom. Click it, then click **Update now**. VoteBuddy downloads the new version, swaps itself out and restarts. Your vote list is kept. You can also click **Check for updates** at any time.

If the update includes a new browser extension, VoteBuddy tells you to switch VoteBuddy Autofill off and back on.

Coming from v1.0–v1.2? Those don't have the updater yet, so download v1.3 or later once from Releases.

**Publishing a new version (for the maintainer):** raise `APP_VERSION` in `votebuddy.pyw`, run `build.bat`, then make a GitHub release whose tag is the same version (e.g. `v1.4`) with `release\VoteBuddy.zip` attached. Everyone's app picks it up.

## Building it yourself

Needs Python 3.12+ on Windows.

```bat
pip install -r requirements.txt pyinstaller
build.bat
```

The finished app is in `release\VoteBuddy.zip`.

Microsoft Store package: `python store\build_store.py`, which makes `release\VoteBuddy-<version>.msix`. See `store\SUBMISSION.md` for the Partner Center steps. Privacy policy: [PRIVACY.md](PRIVACY.md). You can also run it without building: `pythonw votebuddy.pyw`.

## How it works (for the curious)

- `votebuddy.pyw` is the app (Python + CustomTkinter).
- `extension/` is a small browser extension. VoteBuddy opens links with a tag after the `#` (for example `#vbuser=Steve`). That part of a link stays in your browser and is never sent to the website. The extension only acts on tabs with that tag.
- Phone alerts are sent to `https://ntfy.sh` with only the site names and vote links. Your username is not included.
- The extension sends site timers to the app at `http://127.0.0.1:47615`. That address only works on your own PC, and the app only accepts it from a browser extension, not from websites.
