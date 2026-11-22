// VoteBuddy Autofill: only acts on tabs the VoteBuddy app opened (it adds
// "#vbuser=<name>" to the link, plus "&vbsubmit=1" if auto-submit is on).
// Finds the Minecraft-username box and types the name in. If auto-submit is
// on AND the page has no captcha / "verify you're human" check, it clicks
// the vote button once. If there is a captcha it never touches it - it just
// tells you to tick it yourself.
(() => {
  const USER_TAG = /(^|[#&])vbuser=([^&]*)/;
  const SUBMIT_TAG = /(^|[#&])vbsubmit=1/;
  const SITE_TAG = /(^|[#&])vbsite=([^&]*)/;

  // Score every visible text box; the best "username-looking" one wins.
  function findBox() {
    const words = [
      [/minecraft|mc[\s_-]?name/i, 10],
      [/user[\s_-]?name|benutzername|pseudo/i, 9],
      [/\bign\b|in[\s-]?game/i, 8],
      [/nick/i, 7],
      [/player/i, 6],
      [/\bname\b/i, 4],
    ];
    const bad = /e-?mail|pass|search|comment|message|coupon|captcha|discord|code/i;
    let best = null, bestScore = 0;
    for (const el of document.querySelectorAll("input")) {
      const t = (el.getAttribute("type") || "text").toLowerCase();
      if (t !== "text" || el.disabled || el.readOnly) continue;
      if (!visible(el)) continue;
      let own = [el.name, el.id, el.placeholder, el.getAttribute("aria-label"),
                 el.getAttribute("autocomplete"), el.title].join(" ");
      if (el.labels) for (const l of el.labels) own += " " + l.innerText;
      const box = el.parentElement && el.parentElement.parentElement;
      const near = box ? (box.innerText || "").slice(0, 200) : "";
      let s = 0;
      for (const [re, w] of words) {
        if (re.test(own)) s += w; else if (re.test(near)) s += w / 2;
      }
      if (bad.test(own)) s -= 20;
      if (s > bestScore) { bestScore = s; best = el; }
    }
    return bestScore >= 4 ? best : null;
  }

  function visible(el) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none";
  }

  // Is there any captcha / human check on the page? When unsure, say yes.
  function captchaPresent() {
    const provider = /recaptcha|hcaptcha|turnstile|challenges\.cloudflare|arkoselabs|funcaptcha|geetest|friendlycaptcha|captcha/i;
    const inBadge = (el) => el.closest(".grecaptcha-badge"); // invisible reCAPTCHA: no box to tick
    for (const f of document.querySelectorAll("iframe")) {
      const src = f.getAttribute("src") || f.getAttribute("data-src") || "";
      if (!provider.test(src) && !provider.test(f.title || "")) continue;
      if (/[?&]size=invisible/.test(src) || inBadge(f)) continue;
      return true;
    }
    const sel = ".g-recaptcha, .h-captcha, .cf-turnstile, .frc-captcha, [data-sitekey]," +
                " [id*='captcha' i], [class*='captcha' i], [name*='captcha' i]";
    for (const el of document.querySelectorAll(sel)) {
      if (inBadge(el)) continue;
      if ((el.getAttribute("data-size") || "") === "invisible") continue;
      if (el.matches("script, style, link, meta")) continue;
      return true;
    }
    const text = (document.body && document.body.innerText) || "";
    return /verify (that )?you('| a)re (a )?human|i'?m not a robot|are you a robot|human verification/i.test(text);
  }

  // The button that sends the vote: prefer the username box's own form.
  function findSubmit(box) {
    const good = /vote|submit|send|confirm|go\b/i;
    const bad = /log ?in|sign|register|search|cancel|report|share|discord|copy|reset|close|back/i;
    const pick = (els, needWord) => {
      let best = null, bestScore = 0;
      for (const el of els) {
        if (el.disabled || !visible(el)) continue;
        const text = (el.innerText || el.value || el.getAttribute("aria-label") || el.title || "").trim();
        if (bad.test(text)) continue;
        const isSubmit = (el.getAttribute("type") || (el.tagName === "BUTTON" ? "submit" : "")).toLowerCase() === "submit";
        let s = (good.test(text) ? 10 : 0) + (isSubmit ? 5 : 0);
        if (needWord && !good.test(text)) s = 0;
        if (s > bestScore) { bestScore = s; best = el; }
      }
      return bestScore >= 5 ? best : null;
    };
    const btnSel = "button, input[type=submit], input[type=button], a.btn, [role=button]";
    if (box.form) {
      const b = pick(box.form.querySelectorAll(btnSel), false);
      if (b) return b;
    }
    return pick(document.querySelectorAll(btnSel), true); // outside a form: must say vote/submit
  }

  // ---- reading the site's own "vote again in ..." timer ----
  let cooldownFound = false;

  // "23 hours, 14 minutes" / "5h 3m" / "1 day 2 hours" / "12:34:56" -> seconds
  function parseDuration(s) {
    // A negative timer ("-5 hours", "-00:12:30") means the site's countdown ran past zero:
    // the wait is over, so it isn't a real timer.
    if (/(^|[^\w-])[-−]\s*\d/.test(s)) return null;
    const unit = { d: 86400, h: 3600, m: 60, s: 1 };
    const re = /(\d+(?:\.\d+)?)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b/gi;
    let t = 0, found = false, m;
    while ((m = re.exec(s))) { t += parseFloat(m[1]) * unit[m[2][0].toLowerCase()]; found = true; }
    if (found) return t;
    const c = s.match(/^\D{0,12}(\d{1,2}):(\d{2})(?::(\d{2}))?\b/); // clock style right after the words
    if (c) return c[3] !== undefined ? +c[1] * 3600 + +c[2] * 60 + +c[3] : +c[1] * 3600 + +c[2] * 60;
    return null;
  }

  // Looks for "vote again in ...", "next vote in ...", "please wait ..." etc.
  function findCooldown() {
    const text = ((document.body && document.body.innerText) || "").replace(/\s+/g, " ");
    const key = /(vote again|voting again|next vote|can vote in|vote available|available again|again in|come back in|try again in|please wait|you must wait|wait another|cooldown|time left|time remaining|already voted)/gi;
    let m;
    while ((m = key.exec(text))) {
      let after = text.slice(m.index + m[0].length, m.index + m[0].length + 70);
      after = after.split(/[.!?](\s|$)/)[0];
      const secs = parseDuration(after);
      if (secs !== null && secs >= 60 && secs <= 7 * 86400) return Math.round(secs);
    }
    return null;
  }

  // Keep looking for ~40s: many sites only show the timer after you vote.
  function watchCooldown() {
    if (window !== top) return;
    let n = 0;
    const timer = setInterval(() => {
      const secs = findCooldown();
      if (secs) {
        clearInterval(timer);
        cooldownFound = true;
        try { chrome.runtime.sendMessage({ type: "cooldown", seconds: secs }); } catch (e) {}
        const h = Math.floor(secs / 3600), mi = Math.round((secs % 3600) / 60);
        toast(`Site says next vote in ${h}h ${mi}m - sent to VoteBuddy.`, "#2563eb");
      } else if (++n > 26) {
        clearInterval(timer);
      }
    }, 1500);
  }

  // Set the value the way a real keypress would, so React/Vue sites notice.
  function setValue(el, value) {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    el.focus();
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.blur();
  }

  // Little note in the corner of the page so you know what VoteBuddy did.
  function toast(msg, color) {
    if (window !== top) return;
    const d = document.createElement("div");
    d.textContent = "VoteBuddy: " + msg;
    d.className = "votebuddy-toast";
    const stack = document.querySelectorAll(".votebuddy-toast").length;
    d.style.cssText = `position:fixed;top:${16 + stack * 70}px;right:16px;z-index:2147483647;max-width:340px;` +
      "padding:12px 16px;border-radius:14px;font:600 14px/1.35 Segoe UI,sans-serif;color:#fff;" +
      "box-shadow:0 6px 24px rgba(0,0,0,.35);background:" + color;
    document.documentElement.appendChild(d);
    setTimeout(() => d.remove(), 9000);
  }

  // Captcha widgets often load a second or two late, so look a few times
  // before deciding there isn't one.
  function maybeSubmit(box) {
    if (window !== top) return; // can't see the whole page from inside a frame
    let checks = 0;
    const timer = setInterval(() => {
      if (cooldownFound) { // site says you already voted - don't click anything
        clearInterval(timer);
        return;
      }
      if (captchaPresent()) {
        clearInterval(timer);
        toast("Name filled. This site has a captcha - tick it, then click Vote.", "#b45309");
        return;
      }
      if (++checks < 4) return; // ~4s of no captcha
      clearInterval(timer);
      const btn = findSubmit(box);
      if (!btn) {
        toast("Name filled. Couldn't find the Vote button - click it yourself.", "#b45309");
        return;
      }
      toast("No captcha here - submitted your vote.", "#15803d");
      btn.click();
    }, 1000);
  }

  function fillLoop(user, submit) {
    let tries = 0;
    const timer = setInterval(() => {
      const el = findBox();
      if (el) {
        if (el.value !== user) setValue(el, user);
        clearInterval(timer);
        try { chrome.runtime.sendMessage({ type: "filled" }); } catch (e) {}
        if (submit) maybeSubmit(el);
      } else if (++tries > 30) {
        clearInterval(timer); // ~22s: give up quietly, user can Ctrl+V
      }
    }, 750);
  }

  const m = window === top && location.hash.match(USER_TAG);
  if (m) {
    const user = decodeURIComponent(m[2]).trim();
    const submit = SUBMIT_TAG.test(location.hash);
    const sm = location.hash.match(SITE_TAG);
    const site = sm ? decodeURIComponent(sm[2]) : "";
    // strip our tags so the site sees its normal address
    const rest = location.hash.replace(USER_TAG, "").replace(SUBMIT_TAG, "")
      .replace(SITE_TAG, "").replace(/^#&?/, "");
    history.replaceState(history.state, "", location.pathname + location.search + (rest ? "#" + rest : ""));
    if (!user) return;
    chrome.runtime.sendMessage({ type: "claim", user, site });
    if (site) watchCooldown();
    fillLoop(user, submit);
  } else {
    // redirected page, page after voting, reload, or a vote form inside an
    // iframe. Iframes can load before the main page has told us the
    // username, so they ask a few times.
    let asks = window === top ? 1 : 4;
    const ask = () => chrome.runtime.sendMessage({ type: "whoami" }, (res) => {
      if (chrome.runtime.lastError) return;
      if (res && res.site) watchCooldown(); // e.g. "thanks, vote again in 24h" page
      if (res && res.user) fillLoop(res.user, false);
      else if (!(res && res.site) && --asks > 0) setTimeout(ask, 1000);
    });
    ask();
  }
})();
