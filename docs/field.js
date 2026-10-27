// The hero field: skill text on the left, dense and unread, thinning to the right where
// each row resolves into a pinned id. Characters snap to the theme's 8 by 16 cell.
(() => {
  const hero = document.querySelector('.hero');
  const canvas = hero && hero.querySelector('.field');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const css = getComputedStyle(document.documentElement);
  const tint = ['--color-glyph-1', '--color-glyph-2', '--color-glyph-3', '--color-glyph-4', '--color-accent-deep'].map((v) => css.getPropertyValue(v).trim());
  const font = css.getPropertyValue('--font-outlier').trim();
  const RAMP = ' .:-=+*#%@';
  const WORDS = ['name:', 'description:', 'curl', '|', 'sh', 'sudo', 'rm', '-rf', '~/.ssh', 'SKILL.md', 'hooks.json', 'allowed-tools:'];
  const PINS = ['@f1f498bd7d', 'sha256:9c1e', 'pinned', 'scanned: none', 'approve', 'low'];
  const CW = 8, CH = 16, CALM = 96;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let w = 0, h = 0, cols = 0, rows = 0, t = 0, last = 0, raf = 0, running = false;

  const hash = (x, y) => { const s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453; return s - Math.floor(s); };
  const smooth = (x, y) => {
    const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi;
    const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
    const a = hash(xi, yi), b = hash(xi + 1, yi), c = hash(xi, yi + 1), d = hash(xi + 1, yi + 1);
    return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
  };

  // The text, the panel and the pause control stay clear, and the field calms over CALM px around them.
  let boxes = [];
  const measure = () => {
    const r = hero.getBoundingClientRect();
    boxes = [...hero.querySelectorAll('.hero__text, .hero__visual, .field-toggle')].map((el) => {
      const b = el.getBoundingClientRect();
      return [b.left - r.left - 16, b.top - r.top - 16, b.right - r.left + 16, b.bottom - r.top + 16];
    });
  };
  const calm = (x, y) => {
    let d = Infinity;
    for (const [l, tp, rt, bt] of boxes) {
      const dx = Math.max(l - x, 0, x - rt), dy = Math.max(tp - y, 0, y - bt);
      d = Math.min(d, Math.hypot(dx, dy));
    }
    return Math.min(1, d / CALM);
  };

  const size = () => {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = hero.clientWidth; h = hero.clientHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    cols = Math.ceil(w / CW); rows = Math.ceil(h / CH);
    measure();
  };

  const draw = () => {
    ctx.clearRect(0, 0, w, h);
    ctx.font = `12px ${font}`;
    ctx.textBaseline = 'top';
    for (let r = 0; r < rows; r++) {
      // Dense runs spell whole words from the start, so no word is cut mid-way.
      let word = '', at = 0, runs = 0;
      for (let c = 0; c < cols; c++) {
        const x = c * CW, y = r * CH;
        const k = calm(x, y);
        let n = k === 0 ? 0 : (smooth(c * 0.09 + t * 0.35, r * 0.18 - t * 0.1) * (1.15 - c / cols) - 0.2) * k;
        if (n <= 0.05) { word = ''; continue; }
        n = Math.min(n, 0.999);
        let ch;
        if (n > 0.55) {
          if (!word) { word = WORDS[(r * 5 + runs++) % WORDS.length] + ' '; at = 0; }
          ch = word[at++];
          if (at >= word.length) word = WORDS[(r * 5 + runs++) % WORDS.length] + ' ', at = 0;
        } else {
          word = '';
          ch = RAMP[Math.floor(n * RAMP.length)];
        }
        ctx.fillStyle = tint[Math.min(tint.length - 1, Math.floor(n * tint.length))];
        ctx.fillText(ch, x, y);
      }
      if (r % 9 === 4) {
        const label = PINS[(r / 9 | 0) % PINS.length];
        const x = Math.floor((w * 0.94 - label.length * CW) / CW) * CW, y = r * CH;
        if (calm(x, y) === 1 && calm(x + label.length * CW, y) === 1) { ctx.fillStyle = tint[3]; ctx.fillText(label, x, y); }
      }
    }
  };

  const tick = (now) => {
    if (!running) return;
    if (now - last > 1000 / 30) { t += 0.012; last = now; draw(); }
    raf = requestAnimationFrame(tick);
  };

  const state = { seen: false, hover: false, focus: false, held: reduced.matches };
  const btn = hero.querySelector('[data-loop-toggle]');
  const sync = () => {
    const run = state.seen && !state.hover && !state.focus && !state.held;
    if (run && !running) { running = true; raf = requestAnimationFrame(tick); }
    if (!run && running) { running = false; cancelAnimationFrame(raf); }
    if (btn) {
      btn.textContent = state.held ? 'Play' : 'Pause';
      btn.setAttribute('aria-label', state.held ? 'Play animation' : 'Pause animation');
    }
  };
  btn && btn.addEventListener('click', () => { state.held = !state.held; sync(); });
  [['pointerenter', 'hover', true], ['pointerleave', 'hover', false], ['focusin', 'focus', true], ['focusout', 'focus', false]]
    .forEach(([ev, k, v]) => hero.addEventListener(ev, () => { state[k] = v; sync(); }));
  new IntersectionObserver(([e]) => { state.seen = e.isIntersecting; sync(); }).observe(hero);
  new ResizeObserver(() => { size(); draw(); }).observe(hero);
  document.fonts && document.fonts.ready.then(() => { measure(); draw(); });
  size(); draw();
})();

// Install tabs: arrow keys move between them; without script both commands show.
document.querySelectorAll('[data-switch]').forEach((root) => {
  const tabs = [...root.querySelectorAll('[role="tab"]')];
  const select = (tab, focus) => {
    tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute('aria-selected', on);
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
    });
    if (focus) tab.focus();
  };
  tabs.forEach((tab, i) => {
    tab.addEventListener('click', () => select(tab));
    tab.addEventListener('keydown', (e) => {
      const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (step) { e.preventDefault(); select(tabs[(i + step + tabs.length) % tabs.length], true); }
    });
  });
  select(tabs.find((t) => t.getAttribute('aria-selected') === 'true') || tabs[0]);
});

const copyStatus = document.querySelector('[data-copy-status]');
document.querySelectorAll('[data-copy]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    const el = document.getElementById(btn.dataset.copy);
    try {
      await navigator.clipboard.writeText(el.textContent);
      btn.textContent = 'Copied';
      if (copyStatus) copyStatus.textContent = 'Copied to the clipboard.';
    } catch {
      getSelection().selectAllChildren(el);
      btn.textContent = 'Selected';
      if (copyStatus) copyStatus.textContent = 'Copying was blocked. The command is selected; press Command or Control and C.';
    }
    setTimeout(() => { btn.textContent = 'Copy'; }, 1600);
  });
});
