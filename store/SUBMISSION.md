# Putting VoteBuddy on the Microsoft Store

You do steps 1–4 and 6, because they need your identity and your clicks. Claude does step 5, building the package.

## 1. Make a developer account (one time)
1. Go to **https://partner.microsoft.com/dashboard** and sign in with your Microsoft account.
2. Sign up for the **Windows & Xbox** (Microsoft Store) program as an **Individual**. Registration for individuals is free now; if a fee shows up, it's one-time.
3. Finish the identity check (photo ID plus a selfie). Approval can take a day or two.

## 2. Reserve the name
Go to **Apps and games → New product → MSIX or PWA app** and reserve **VoteBuddy**. If that name is taken, pick another and tell Claude, so `display_name` in `store_config.json` matches.

## 3. Copy the product identity
Open the app's **Product management → Product identity** page. Copy these three values into `store/store_config.json`, or paste them to Claude:

| Partner Center shows | Goes in store_config.json as |
| --- | --- |
| Package/Identity/Name | `identity_name` |
| Package/Identity/Publisher (starts with `CN=`) | `publisher` |
| Package/Properties/PublisherDisplayName | `publisher_display_name` |

## 4. Fill in the submission (Start your submission)
- **Pricing and availability:** Free, all markets (or just the ones you want).
- **Properties:**
  - Category: **Utilities + tools**.
  - Privacy policy URL: `https://github.com/monkeysoto/VoteBuddy/blob/main/PRIVACY.md`
  - Website: `https://github.com/monkeysoto/VoteBuddy`
- **Age ratings:** answer the questionnaire. Say no to violence and other content, no user-to-user chat, and no purchases. The app does contain a promotion for a Minecraft server, which counts as in-app ads only if the questionnaire asks; answer honestly.
- **Submission options → restricted capabilities:** the package uses `runFullTrust`. When asked why, paste:
  > VoteBuddy is a regular Win32 desktop app (Python/Tkinter) packaged with the Desktop Bridge. runFullTrust is required for any packaged Win32 desktop app.

## 5. Build the package (Claude)
```
python store\build_store.py
```
This makes `release\VoteBuddy-<version>.msix`. Upload it under **Packages**.

## 6. Store listing (English)
**Product name:** VoteBuddy

**Description:**
> Never miss a Minecraft server vote reward again.
>
> VoteBuddy keeps all your server vote links in one list with a countdown for each one. When a vote is ready, it opens the page in your browser, fills in your Minecraft username and, on sites without a captcha, clicks Vote for you. It even reads each site's "vote again in…" timer so your countdowns are exact.
>
> • Countdown for every vote site
> • Opens vote pages with your username already filled in (with the free VoteBuddy Autofill browser extension)
> • Clicks Vote for you on sites without a captcha. VoteBuddy never solves captchas.
> • Reads the site's own "next vote" timer
> • Phone alerts on iPhone and Android through the free ntfy app
> • Dark mode and start with Windows
>
> Not an official Minecraft product. Not approved by or associated with Mojang or Microsoft.

**Short description:** Track your Minecraft server vote links and never miss a vote reward.

**Keywords:** minecraft, vote, server, reminder, rewards, timer, voting

**Screenshots:** use the PNG files in `store/screenshots/` (1366×768).

**Support contact:** `https://github.com/monkeysoto/VoteBuddy/issues`

Click **Submit to the Store**. Certification usually takes a few hours to 3 days. If Microsoft asks for changes, paste their message to Claude.

## Updating later
Raise `APP_VERSION`, rebuild with `python store\build_store.py`, then make a new submission in Partner Center with the new `.msix`. Store users get the update automatically. The GitHub `.exe` version keeps its own updater.
