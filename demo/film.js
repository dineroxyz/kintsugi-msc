// Film the viva fallback videos.
//
//   node demo/film.js [outdir]      (console must be running on :8100)
//
// Terminal segments replay demo/recordings/*.json (made by demo/record.py
// from real runs) at their recorded pace. Website segments are filmed live
// in a browser against the console. Output: MP4 files in outdir.
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const puppeteer = require(process.env.PUPPETEER || 'puppeteer');

const ROOT = path.resolve(__dirname, '..');
const OUT = process.argv[2] || path.join(ROOT, 'demo', 'videos');
const BASE = process.env.CONSOLE_URL || 'http://127.0.0.1:8100';
const W = +(process.env.FILM_W || 1280), H = +(process.env.FILM_H || 720);
const sleep = ms => new Promise(r => setTimeout(r, ms));
fs.mkdirSync(OUT, { recursive: true });

function playerHtml(rec) {
  return `<!doctype html><html><head><meta charset="utf-8"><style>
  @font-face { font-family: Serif; src: url("${BASE}/static/fonts/shippori-mincho-latin-700-normal.woff2"); }
  html,body { margin:0; width:${W}px; height:${H}px; background:#17140F; color:#EDE6D8;
    font-family: -apple-system, system-ui, sans-serif; overflow:hidden; }
  .card { position:absolute; inset:0; display:grid; place-content:center; text-align:left;
    padding:0 120px; transition: opacity .6s; }
  .card h1 { font: 700 54px/1.1 Serif, Georgia, serif; margin:0 0 18px; }
  .card h2 { font: 400 28px/1.3 system-ui; margin:0 0 30px; color:#EDE6D8; }
  .card p { color:#B9AE9C; font-size:18px; margin:4px 0; }
  .card code { color:#D9B970; }
  .seam { height:2px; background:#C6A25A; width:520px; margin: 0 0 26px; }
  .term { position:absolute; inset:40px 48px; border:1px solid #3A3229; border-radius:8px;
    background:#120F0B; opacity:0; transition: opacity .5s; display:flex; flex-direction:column; }
  .bar { height:34px; border-bottom:1px solid #3A3229; display:flex; align-items:center;
    padding:0 14px; color:#968B7A; font-size:14px; gap:8px; }
  .dot { width:11px; height:11px; border-radius:50%; background:#3A3229; }
  .screen { flex:1; overflow:hidden; padding:16px 20px; font: 16px/1.5 ui-monospace, Menlo, monospace;
    white-space:pre; }
  .screen .h { color:#D9B970; } .screen .add { color:#7DBB8E; } .screen .del { color:#E07A68; }
  .screen .ok { color:#7DBB8E; } .clock { color:#968B7A; }
  .tag { position:absolute; right:60px; bottom:12px; color:#6f665b; font-size:12px; }
  </style></head><body>
  <div class="card" id="card"><div class="seam"></div><h1>Kintsugi</h1>
  <h2>${rec.title}</h2>
  <p>A recorded run of <code>${rec.command}</code></p>
  <p>Recorded ${rec.recorded}. Played back at the pace it ran.</p></div>
  <div class="term" id="term"><div class="bar"><span class="dot"></span><span class="dot"></span>
  <span class="dot"></span><span>kintsugi  ${rec.command}</span></div><div class="screen" id="screen"></div></div>
  <div class="tag">Kintsugi by Abe Oluwaseyi Elizabeth</div>
  <script>
  const rec = ${JSON.stringify(rec)};
  function cls(l) {
    if (l.startsWith('==')) return 'h';
    if (/^  \\+/.test(l) && !l.startsWith('  +++')) return 'add';
    if (/^  -/.test(l) && !l.startsWith('  ---')) return 'del';
    if (/^Outcome: (recovered|refused)|True$|tampered$/.test(l)) return 'ok';
    return '';
  }
  window.play = async () => {
    const screen = document.getElementById('screen');
    await new Promise(r => setTimeout(r, 3200));
    document.getElementById('card').style.opacity = 0;
    document.getElementById('term').style.opacity = 1;
    await new Promise(r => setTimeout(r, 700));
    const t0 = performance.now(); let clock = null, clockStart = 0;
    for (let i = 0; i < rec.lines.length; i++) {
      const [t, text] = rec.lines[i];
      while ((performance.now() - t0) / 1000 < t) {
        if (clock) clock.textContent = '  ' + ((performance.now() - t0) / 1000 - clockStart).toFixed(0) + ' s elapsed';
        await new Promise(r => setTimeout(r, 100));
      }
      if (clock) { clock.remove(); clock = null; }
      const div = document.createElement('div');
      div.textContent = text || ' '; div.className = cls(text);
      screen.appendChild(div);
      if (text === 'Running...') {
        clock = document.createElement('div'); clock.className = 'clock';
        screen.appendChild(clock); clockStart = t;
      }
      screen.scrollTop = screen.scrollHeight;
    }
    await new Promise(r => setTimeout(r, 4000));
  };
  </script></body></html>`;
}

async function film(page, file, action) {
  // Record with the browser's own frame timestamps, so the video plays back
  // at exactly the pace things happened (a fixed-rate recorder would drop
  // idle frames and speed playback up).
  const dir = fs.mkdtempSync(path.join(OUT, '.frames-'));
  const cdp = await page.createCDPSession();
  const frames = [];
  cdp.on('Page.screencastFrame', async f => {
    const n = frames.length;
    fs.writeFileSync(path.join(dir, `f${String(n).padStart(6, '0')}.jpg`), Buffer.from(f.data, 'base64'));
    frames.push(f.metadata.timestamp);
    try { await cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }); } catch (e) {}
  });
  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 92, everyNthFrame: 1 });
  const t0 = Date.now() / 1000;
  await action();
  const t1 = Date.now() / 1000;
  await cdp.send('Page.stopScreencast');
  await sleep(300);
  // concat list: each frame shown until the next one arrived; the last one
  // until the action ended
  const lines = [];
  for (let i = 0; i < frames.length; i++) {
    const until = i + 1 < frames.length ? frames[i + 1] : Math.max(frames[i] + 0.04, t1 - t0 + frames[0]);
    lines.push(`file 'f${String(i).padStart(6, '0')}.jpg'`, `duration ${Math.max(0.001, until - frames[i]).toFixed(4)}`);
  }
  // ffmpeg ignores the last entry's duration, so close with a one-frame copy
  lines.push(`file 'f${String(frames.length - 1).padStart(6, '0')}.jpg'`, 'duration 0.0333');
  fs.writeFileSync(path.join(dir, 'list.txt'), lines.join('\n'));
  const mp4 = path.join(OUT, file + '.mp4');
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i',
    path.join(dir, 'list.txt'), '-t', (t1 - t0).toFixed(3), '-vf', 'fps=30,format=yuv420p,scale=trunc(iw/2)*2:trunc(ih/2)*2',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '20', '-movflags', '+faststart', mp4]);
  fs.rmSync(dir, { recursive: true, force: true });
  console.log('wrote', mp4, `(${frames.length} frames, ${(t1 - t0).toFixed(1)} s)`);
}

async function smoothScroll(page, to, ms = 1600) {
  await page.evaluate(async (to, ms) => {
    const from = window.scrollY, t0 = performance.now();
    await new Promise(done => {
      const step = now => {
        const k = Math.min(1, (now - t0) / ms), e = k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
        window.scrollTo(0, from + (to - from) * e);
        k < 1 ? requestAnimationFrame(step) : done();
      };
      requestAnimationFrame(step);
    });
  }, to, ms);
}

const yOf = (page, sel) => page.$eval(sel, e => e.getBoundingClientRect().top + window.scrollY - 24);

(async () => {
  const browser = await puppeteer.launch({ headless: 'new', protocolTimeout: 180000,
    defaultViewport: { width: W, height: H } });

  // 1. terminal segments, replayed from real recordings
  const order = [['heal', 'demo-2-break-and-heal'], ['replay', 'demo-3-certificate-replay'],
                 ['refusal', 'demo-4-race-refusal']];
  for (const [seg, name] of order) {
    const recPath = path.join(ROOT, 'demo', 'recordings', seg + '.json');
    if (!fs.existsSync(recPath)) { console.log('skip, no recording:', seg); continue; }
    const rec = JSON.parse(fs.readFileSync(recPath, 'utf8'));
    const page = await browser.newPage();
    await page.setContent(playerHtml(rec), { waitUntil: 'domcontentloaded' });
    await sleep(400);
    await film(page, name, () => page.evaluate(() => window.play()));
    await page.close();
  }

  // 2. website segments, filmed live
  const page = await browser.newPage();
  const go = async url => { await page.goto(BASE + url, { waitUntil: 'domcontentloaded' });
    await page.evaluate(() => document.fonts.ready); await sleep(300); };

  await film(page, 'demo-1-home-and-practice', async () => {
    await go('/?theme=dark'); await sleep(4200);            // the bowl's seams draw in
    await smoothScroll(page, await yOf(page, '#how-title'), 1800); await sleep(2500);
    await smoothScroll(page, await yOf(page, '#found-title'), 1800); await sleep(4000);
    await go('/practice?theme=dark'); await sleep(2500);
    await smoothScroll(page, 420, 2200); await sleep(3500);
  });

  await film(page, 'demo-5-results', async () => {
    await go('/results?theme=dark'); await sleep(2500);
    await smoothScroll(page, await yOf(page, '#grid-title'), 1800); await sleep(1500);
    const cells = await page.$$('.mx-row:nth-of-type(5) .mx-cell');
    for (const i of [2, 14, 30, 47, 52]) {
      if (!cells[i]) continue;
      const box = await cells[i].boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 18 });
      await sleep(1300);
    }
    if (cells[2]) { await cells[2].click(); await sleep(3500);
      await page.click('#detail .close'); await sleep(800); }
    await smoothScroll(page, await yOf(page, '#compare-title'), 1800); await sleep(4500);
    await smoothScroll(page, await yOf(page, '#ablation-title'), 1800); await sleep(4500);
  });

  await film(page, 'demo-6-certificate', async () => {
    await go('/certificates?theme=dark'); await sleep(3000);
    await go('/certificates/0?theme=dark'); await sleep(2500);
    const ys = await page.$$eval('.steps > li', ls => ls.map(l => l.getBoundingClientRect().top + window.scrollY - 40));
    for (const y of ys) { await smoothScroll(page, y, 1300); await sleep(2600); }
    await sleep(1500);
  });

  // 3. the Live view, replaying recorded real runs
  for (const [bug, name] of [['F03', 'demo-0-live-heal'], ['F11', 'demo-0-live-refusal']]) {
    await film(page, name, async () => {
      await go('/live?theme=dark'); await sleep(1500);
      await page.select('#live-bug', bug); await sleep(600);
      const replayBtn = await page.$('#live-replay');
      await (replayBtn || await page.$('#live-go')).click();
      await page.waitForFunction(() => document.getElementById('live-go').textContent === 'Break it again',
                                 { timeout: 120000 });
      await sleep(3500);
    });
  }

  await browser.close();
})();
