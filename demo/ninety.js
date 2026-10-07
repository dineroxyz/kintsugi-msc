// The 90-second film: one live repair at the pace it ran, the in-browser
// certificate check, and the weaker-model result, between short title cards.
//
//   node demo/ninety.js [outdir]      (console must be running on :8100)
//
// Every frame of the website parts is the real site; the cards only say
// what the next part shows.
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const puppeteer = require(process.env.PUPPETEER || 'puppeteer');

const ROOT = path.resolve(__dirname, '..');
const OUT = process.argv[2] || path.join(ROOT, 'demo', 'videos');
const BASE = process.env.CONSOLE_URL || 'http://127.0.0.1:8100';
const REPLAY = process.env.FILM_REPLAY || '20261007-210952-M05';
const W = 1280, H = 720;
const sleep = ms => new Promise(r => setTimeout(r, ms));
fs.mkdirSync(OUT, { recursive: true });
const work = fs.mkdtempSync(path.join(OUT, '.ninety-'));

function card(lines, small) {
  return `<!doctype html><html><head><meta charset="utf-8"><style>
  @font-face { font-family: Serif; src: url("${BASE}/static/fonts/shippori-mincho-latin-700-normal.woff2"); }
  html,body { margin:0; width:${W}px; height:${H}px; background:#17140F; color:#EDE6D8;
    font-family: -apple-system, system-ui, sans-serif; overflow:hidden; }
  .c { position:absolute; inset:0; display:grid; place-content:center; padding:0 140px; }
  .seam { height:2px; background:#C6A25A; width:0; margin:0 0 28px; animation: draw 1.2s .2s ease-out forwards; }
  @keyframes draw { to { width: 420px; } }
  h1 { font: 700 50px/1.15 Serif, Georgia, serif; margin:0 0 16px; max-width: 960px; }
  p { color:#B9AE9C; font-size:22px; line-height:1.45; margin:0; max-width: 900px; }
  </style></head><body><div class="c"><div class="seam"></div>
  ${lines.map(l => `<h1>${l}</h1>`).join('')}${small ? `<p>${small}</p>` : ''}</div></body></html>`;
}

async function film(page, file, action) {
  // frames carry the browser's own timestamps, so playback runs at real pace
  const dir = fs.mkdtempSync(path.join(work, 'f-'));
  const cdp = await page.createCDPSession();
  const frames = [];
  cdp.on('Page.screencastFrame', async f => {
    fs.writeFileSync(path.join(dir, `f${String(frames.length).padStart(6, '0')}.jpg`), Buffer.from(f.data, 'base64'));
    frames.push(f.metadata.timestamp);
    try { await cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }); } catch (e) {}
  });
  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 92, maxWidth: W, maxHeight: H });
  const t0 = Date.now() / 1000;
  await action();
  const t1 = Date.now() / 1000;
  await cdp.send('Page.stopScreencast');
  await sleep(300);
  const lines = [];
  for (let i = 0; i < frames.length; i++) {
    const until = i + 1 < frames.length ? frames[i + 1] : Math.max(frames[i] + 0.04, t1 - t0 + frames[0]);
    lines.push(`file 'f${String(i).padStart(6, '0')}.jpg'`, `duration ${Math.max(0.001, until - frames[i]).toFixed(4)}`);
  }
  // ffmpeg ignores the last entry's duration, so close with a one-frame copy
  lines.push(`file 'f${String(frames.length - 1).padStart(6, '0')}.jpg'`, 'duration 0.0333');
  fs.writeFileSync(path.join(dir, 'list.txt'), lines.join('\n'));
  const mp4 = path.join(work, file + '.mp4');
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i',
    path.join(dir, 'list.txt'), '-t', (t1 - t0).toFixed(3), '-vf', `fps=30,scale=${W}:${H}:force_original_aspect_ratio=decrease,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2:color=0x17140F,format=yuv420p`,
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '20', mp4]);
  const got = +execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', mp4]);
  console.log(file, `${(t1 - t0).toFixed(1)} s filmed, ${got.toFixed(1)} s encoded`);
  return mp4;
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

(async () => {
  const browser = await puppeteer.launch({ headless: 'new', protocolTimeout: 180000,
    defaultViewport: { width: W, height: H } });
  const page = await browser.newPage();
  const go = async url => { await page.goto(BASE + url, { waitUntil: 'domcontentloaded' });
    await page.evaluate(() => document.fonts.ready); await sleep(250); };
  const showCard = async (name, lines, small, ms) => {
    await page.setContent(card(lines, small), { waitUntil: 'domcontentloaded' });
    await page.evaluate(() => document.fonts.ready);
    return film(page, name, () => sleep(ms));
  };
  const parts = [];

  parts.push(await showCard('01-title', ['Kintsugi'],
    'Software that repairs itself, but only puts a fix live when it can prove the fix works.', 4500));

  parts.push(await showCard('02-card', ['A real bug, a real repair.'],
    'A recorded run, played at the pace it happened.', 3000));
  parts.push(await film(page, '03-live', async () => {
    await go(`/live?theme=dark&replay=${REPLAY}`);
    await page.waitForFunction(() => /again|another/.test(document.getElementById('live-go').textContent),
                               { timeout: 150000 });
    const ex = await page.$('#live-explain');
    if (ex) { await ex.scrollIntoView(); }
    await sleep(4000);
  }));

  parts.push(await showCard('04-card', ['Every repair is signed.'],
    'Your own browser can check a certificate, without trusting this site.', 3000));
  parts.push(await film(page, '05-verify', async () => {
    await go('/certificates/0?theme=dark');
    const y = await page.$eval('.verify-box', e => e.getBoundingClientRect().top + window.scrollY - 160);
    await smoothScroll(page, y, 1200); await sleep(700);
    await page.click('[data-verify-go]');
    await page.waitForFunction(() => /Verified|could not/.test(document.querySelector('.verify-box').innerText),
                               { timeout: 30000 });
    await sleep(3500);
  }));

  parts.push(await showCard('06-card', ['It makes cheaper AI safe to use.'],
    '360 more tests with a smaller AI model, registered before they ran.', 3000));
  parts.push(await film(page, '07-weak', async () => {
    await go('/?theme=dark');
    const y = await page.$eval('.feat-wide', e => e.getBoundingClientRect().top + window.scrollY - 60);
    await smoothScroll(page, y, 1800); await sleep(8000);
  }));

  parts.push(await showCard('08-end', ['Kintsugi'],
    'By Abe Oluwaseyi Elizabeth. MSc Computer Science.<br>console-production-f3b9.up.railway.app', 5000));
  await browser.close();

  const list = path.join(work, 'parts.txt');
  fs.writeFileSync(list, parts.map(p => `file '${p}'`).join('\n'));
  const out = path.join(OUT, 'kintsugi-film.mp4');
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', list,
    '-vf', 'fps=30,format=yuv420p', '-c:v', 'libx264', '-preset', 'slow', '-crf', '20',
    '-movflags', '+faststart', out]);
  if (!process.env.FILM_KEEP) fs.rmSync(work, { recursive: true, force: true });
  console.log('wrote', out);
})();
