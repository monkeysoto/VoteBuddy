// Remembers which tabs VoteBuddy opened (username + which site in your list)
// so redirects, the "thanks for voting" page, reloads and vote forms inside
// iframes are handled. When a page shows its "vote again in ..." timer, this
// passes it to the VoteBuddy app running on this PC (127.0.0.1 only).
// Entries expire after 10 minutes or when the tab closes.

const APP = "http://127.0.0.1:47615/cooldown";
const TTL = 10 * 60 * 1000;

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  const tab = sender.tab && sender.tab.id;
  if (tab == null) return;
  const key = "tab" + tab;
  const get = () => chrome.storage.session.get(key).then((g) => {
    const e = g[key];
    return e && Date.now() - e.t < TTL ? e : null;
  });

  if (msg.type === "claim") {
    chrome.storage.session.set({ [key]: { user: msg.user, site: msg.site || "", t: Date.now(), filled: false } });
    reply({ ok: true });
  } else if (msg.type === "whoami") {
    get().then((e) => reply(e ? { user: e.filled ? null : e.user, site: e.site || null } : {}));
    return true; // async reply
  } else if (msg.type === "filled") {
    // name is in; later pages in this tab won't be filled again
    get().then((e) => e && chrome.storage.session.set({ [key]: { ...e, filled: true } }));
  } else if (msg.type === "cooldown") {
    get().then((e) => {
      if (!e || !e.site) return;
      fetch(APP, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ site: e.site, seconds: msg.seconds }),
      }).catch(() => {}); // app not running: nothing to update
      chrome.storage.session.set({ [key]: { ...e, site: "" } }); // report once per visit
    });
  }
});

chrome.tabs.onRemoved.addListener((tab) => chrome.storage.session.remove("tab" + tab));
