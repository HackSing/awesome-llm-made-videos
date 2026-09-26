// Runs inside an x.com search-results tab in the maintainer's own logged-in
// browser (driven through Claude in Chrome). Read-only: it scrolls a few
// screens at a human pace and reads what is on the page. No clicks on
// like/follow/reply, no requests of its own.
//
// Usage in the page:  JSON.stringify(await collectX({ screens: 4 }))
// Save the output as {"query", "mode", "screens", "result"} and feed it to
// scripts/x_ingest.py.
//
// The tab must stay visible for the whole capture. In a hidden tab Chrome
// throttles timers and X stops loading more results, so a capture quietly
// shrinks to the first 4-8 posts; `hidden_during_capture` flags that case and
// x_ingest.py should then be given "degraded" for it.
//
// Query strings are stripped from every URL: the browser bridge refuses to
// return results that contain them, and they are tracking noise anyway.

async function collectX({ screens = 4, minGap = 3000, maxGap = 8000, textLimit = 300 } = {}) {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const noQuery = (u) => u.split("?")[0];
  const posts = new Map();
  let hiddenSeen = false;

  const grab = () => {
    if (document.visibilityState !== "visible") hiddenSeen = true;
    for (const a of document.querySelectorAll('article[data-testid="tweet"]')) {
      const time = a.querySelector("time");
      const link = time && time.closest("a");
      if (!link) continue;
      const url = noQuery(link.href);
      if (posts.has(url)) continue;
      const m = url.match(/\/([^/]+)\/status\/(\d+)/);
      const textEl = a.querySelector('[data-testid="tweetText"]');
      const related = [...a.querySelectorAll('a[href*="/status/"]')]
        .map((x) => noQuery(x.href).replace(/\/(photo|video|analytics)(\/.*)?$/, ""))
        .filter((h) => h !== url);
      posts.set(url, {
        handle: m ? m[1] : "",
        id: m ? m[2] : "",
        published_utc: time.getAttribute("datetime"),
        text: (textEl ? textEl.innerText : "").replace(/https?:\/\/\S+/g, noQuery).slice(0, textLimit),
        has_video: !!a.querySelector('[data-testid="videoPlayer"],[data-testid="videoComponent"]'),
        links: [...(textEl ? textEl.querySelectorAll('a[href^="http"]') : [])].map((x) => noQuery(x.innerText.replace(/…$/, ""))),
        related_status: [...new Set(related)],
        stats: (a.querySelector('[role="group"][aria-label]') || {}).ariaLabel || "",
      });
    }
  };

  grab();
  for (let i = 0; i < screens; i++) {
    window.scrollBy(0, window.innerHeight * (0.7 + Math.random() * 0.5));
    await sleep(minGap + Math.random() * (maxGap - minGap));
    grab();
  }
  const body = document.body.innerText;
  return {
    captured_utc: new Date().toISOString(),
    hidden_during_capture: hiddenSeen,
    blocked: /Rate limit exceeded|Something went wrong|Try reloading/i.test(body) && posts.size === 0,
    posts: [...posts.values()],
  };
}
