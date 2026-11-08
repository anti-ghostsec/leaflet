// Leaflet's window: toolbar, dialogs, keyboard, and the link to leaflet.py.
// The PDF view (pdfview.mjs) and the document editor (docedit.mjs) are
// loaded only when a file of their kind is opened.
import { icon } from './icons.mjs';

const $ = (sel) => document.querySelector(sel);
const body = document.body;

let mode = null;            // the open PdfMode or DocMode
let dirty = false;
let state = { recent: [] };
let pdfopsModule = null;

const IS_TEST = new URLSearchParams(location.search).has('test');

// ------------------------------------------------------------------ talking to leaflet.py

function post(cmd, data = {}) {
  window.webkit.messageHandlers.leaflet.postMessage(JSON.stringify({ cmd, ...data }));
}

async function put(data, type = 'application/octet-stream') {
  const resp = await fetch('/put', { method: 'POST', body: data, headers: { 'Content-Type': type } });
  if (!resp.ok) throw new Error('the file is too large');
  return resp.text();
}

async function take(token) {
  const resp = await fetch(`/blob/${token}`);
  if (!resp.ok) throw new Error('the file went missing');
  return new Uint8Array(await resp.arrayBuffer());
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error(`couldn't load ${src}`));
    document.head.append(s);
  });
}

async function pdfops() {
  if (!globalThis.PDFLib) await loadScript('vendor/pdf-lib.min.js');
  pdfopsModule ||= await import('./pdfops.mjs');
  return pdfopsModule;
}

// ------------------------------------------------------------------ the app object the modes use

const app = {
  post, put, take, pdfops,
  setDirty(value) {
    if (dirty === value) return;
    dirty = value;
    $('#toolbar .save').classList.toggle('dirty', dirty);
    post('title', { dirty });
  },
  get dirty() { return dirty; },
  status(left, right) {
    if (left != null) $('#status-left').textContent = left;
    if (right != null) $('#status-right').textContent = right;
  },
  error(text) { return dialog({ title: 'Leaflet', text, buttons: [{ label: 'OK', value: true, primary: true }] }); },
  dialog: (opts) => dialog(opts),
  menu: (anchor, items) => showMenu(anchor, items),
  banner(text) {
    $('#banner-text').textContent = text || '';
    $('#banner').hidden = !text;
  },
  busy(on) { body.style.cursor = on ? 'progress' : ''; },
  sidebarShown: () => !$('#sidebar').hidden,
  findCount(text, notFound) {
    $('#find-count').textContent = text || '';
    $('#find').classList.toggle('notfound', !!notFound);
  },
  log(text) { if (IS_TEST) console.log(text); },
};
window.LeafletApp = app;      // for the tests

// ------------------------------------------------------------------ toolbar

for (const btn of document.querySelectorAll('[data-cmd]')) {
  if (!btn.closest('#welcome') && !btn.classList.contains('plain')) btn.innerHTML = icon(btn.dataset.cmd) || btn.innerHTML;
  btn.setAttribute('aria-label', btn.title || btn.textContent);
  btn.addEventListener('click', (e) => run(btn.dataset.cmd, e.currentTarget));
  btn.addEventListener('mousedown', (e) => { if (!btn.closest('#welcome, .dialog')) e.preventDefault(); });   // keep the caret
}

async function run(cmd, anchor) {
  try {
    switch (cmd) {
      case 'open': post('open'); return;
      case 'new': post('new'); return;
      case 'save': await save(false); return;
      case 'menu': fileMenu(anchor); return;
      case 'sidebar': toggleSidebar(); return;
      case 'settings': settings(); return;
      case 'banner-close': app.banner(''); return;
      case 'find-next': find(false, true); return;
      case 'find-prev': find(true, true); return;
      default: if (mode) await mode.command(cmd, anchor);
    }
  } catch (e) {
    app.busy(false);
    app.error(String(e && e.message || e));
  }
}

async function save(as, format) {
  if (!mode) return;
  await mode.save(as, format);
}

function fileMenu(anchor) {
  const items = [];
  if (mode && mode.kind === 'doc') {
    items.push(['save-as', 'Save as Word document (.docx)…', () => save(true, 'docx')],
      ['save-as', 'Save as OpenDocument (.odt)…', () => save(true, 'odt')],
      ['save-as', 'Save as plain text (.txt)…', () => save(true, 'txt')],
      ['export', 'Export as PDF…', () => mode.command('export-pdf'), 'Ctrl+E'], null);
  } else if (mode) {
    items.push(['save-as', 'Save as…', () => save(true), 'Ctrl+Shift+S'], null);
  }
  if (mode) items.push(['print', 'Print…', printDoc, 'Ctrl+P']);
  items.push(['new', 'New document', () => post('new'), 'Ctrl+N'],
    ['open', 'Open…', () => post('open'), 'Ctrl+O'], null,
    [null, 'Keyboard shortcuts', shortcuts, 'F1'],
    [null, 'About Leaflet and privacy', about], null,
    ['close', 'Close window', () => post('close'), 'Ctrl+W']);
  showMenu(anchor, items);
}

// items: [iconName, label, action, shortcut] or null for a separator
function showMenu(anchor, items) {
  const menu = $('#menu');
  menu.innerHTML = '';
  for (const item of items) {
    if (!item) { menu.append(document.createElement('hr')); continue; }
    const [ic, label, action, kbd] = item;
    const b = document.createElement('button');
    b.setAttribute('role', 'menuitem');
    b.innerHTML = `${ic ? icon(ic) : '<svg viewBox="0 0 24 24"></svg>'}<span></span>${kbd ? `<span class="kbd">${kbd}</span>` : ''}`;
    b.querySelector('span').textContent = label;
    b.addEventListener('mousedown', (e) => e.preventDefault());
    b.addEventListener('click', () => { closeMenu(); Promise.resolve(action()).catch((e) => app.error(String(e.message || e))); });
    menu.append(b);
  }
  menu.hidden = false;
  const r = anchor && anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : { left: anchor?.x || 0, bottom: anchor?.y || 0 };
  const w = menu.offsetWidth, h = menu.offsetHeight;
  menu.style.left = `${Math.max(4, Math.min(r.left, innerWidth - w - 4))}px`;
  menu.style.top = `${Math.max(4, Math.min(r.bottom + 2, innerHeight - h - 4))}px`;
  menu.querySelector('button')?.focus();
}

function printDoc() {
  if (mode && mode.print) return mode.print();
  post('print');
  return undefined;
}

function closeMenu() { $('#menu').hidden = true; }

document.addEventListener('mousedown', (e) => {
  if (!$('#menu').hidden && !e.target.closest('#menu')) closeMenu();
});
$('#menu').addEventListener('keydown', (e) => {
  const buttons = [...$('#menu').querySelectorAll('button')];
  const i = buttons.indexOf(document.activeElement);
  if (e.key === 'ArrowDown') { buttons[(i + 1) % buttons.length].focus(); e.preventDefault(); }
  if (e.key === 'ArrowUp') { buttons[(i - 1 + buttons.length) % buttons.length].focus(); e.preventDefault(); }
});

function toggleSidebar(show) {
  const sb = $('#sidebar');
  sb.hidden = show === undefined ? !sb.hidden : !show;
  $('[data-cmd="sidebar"]').classList.toggle('on', !sb.hidden);
  mode?.sidebarChanged?.(!sb.hidden);
}

// ------------------------------------------------------------------ dialogs

function dialog({ title, text, html, buttons = [{ label: 'OK', value: true, primary: true }], input, checkbox }) {
  return new Promise((resolve) => {
    const modal = $('#modal');
    const box = modal.querySelector('.dialog');
    box.innerHTML = '';
    if (title) { const h = document.createElement('h2'); h.textContent = title; box.append(h); }
    if (text) for (const para of String(text).split('\n\n')) { const p = document.createElement('p'); p.textContent = para; box.append(p); }
    if (html) { const d = document.createElement('div'); d.innerHTML = html; box.append(d); }
    let field = null;
    if (input) {
      field = document.createElement('input');
      field.type = input.type || 'text';
      field.value = input.value || '';
      field.placeholder = input.placeholder || '';
      field.setAttribute('aria-label', input.label || title || '');
      box.append(field);
    }
    const row = document.createElement('div');
    row.className = 'buttons';
    const finish = (value) => {
      modal.hidden = true;
      document.removeEventListener('keydown', onKey, true);
      resolve(field && value !== null && value !== false ? (value === true ? field.value : value) : value);
    };
    for (const b of buttons) {
      const el = document.createElement('button');
      el.textContent = b.label;
      if (b.primary) el.className = 'primary';
      el.addEventListener('click', () => finish(b.value));
      row.append(el);
    }
    box.append(row);
    const cancel = buttons.find((b) => b.value === null || b.value === false);
    const primary = buttons.find((b) => b.primary);
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(cancel ? cancel.value : null); }
      if (e.key === 'Enter' && primary && (!e.target.closest('button') || e.target === field)) {
        e.preventDefault(); e.stopPropagation(); finish(primary.value);
      }
    };
    document.addEventListener('keydown', onKey, true);
    modal.hidden = false;
    (field || row.querySelector('.primary') || row.querySelector('button')).focus();
    if (field) field.select();
    if (checkbox) checkbox(box);
  });
}

function shortcuts() {
  const rows = [
    ['Open / New document / Save / Save as', 'Ctrl+O / Ctrl+N / Ctrl+S / Ctrl+Shift+S'],
    ['Print / Close window / Quit', 'Ctrl+P / Ctrl+W / Ctrl+Q'],
    ['Find / next / previous', 'Ctrl+F / F3 / Shift+F3'],
    ['Zoom in / out / fit width', 'Ctrl+= / Ctrl+− / Ctrl+0'],
    ['Pages or outline / Full screen', 'F9 / F11'],
    ['PDF: go to page / first / last', 'Ctrl+G / Home / End'],
    ['PDF: turn pages right / left', 'Ctrl+R / Ctrl+Shift+R'],
    ['PDF: tools — select, highlight, note, draw', 'Esc, H, N, D'],
    ['PDF page list: select / delete / move', 'Click, Shift/Ctrl+click / Delete / Alt+↑↓ or drag'],
    ['Document: bold / italic / underline', 'Ctrl+B / Ctrl+I / Ctrl+U'],
    ['Document: text / heading 1–3', 'Ctrl+0 / Ctrl+1–3'],
    ['Document: bullets / numbering', 'Ctrl+Shift+8 / Ctrl+Shift+7'],
    ['Document: align left / centre / right / justify', 'Ctrl+L / Ctrl+E / Ctrl+R / Ctrl+J'],
    ['Document: page break / export as PDF', 'Ctrl+Enter / Ctrl+Shift+E'],
  ];
  const esc = (s) => s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]);
  dialog({ title: 'Keyboard shortcuts',
    html: `<table style="border-collapse:collapse">${rows.map(([a, k]) => `<tr><td style="padding:3px 16px 3px 0">${esc(a)}</td><td style="padding:3px 0;color:var(--muted)">${esc(k)}</td></tr>`).join('')}</table>` });
}

function about() {
  dialog({ title: 'About Leaflet',
    html: `<p>Leaflet opens PDFs and word-processor documents, and nothing else happens behind your back.</p>
<ul><li>No network: this window can't reach the internet. Links open in your browser only when you click them and say yes.</li>
<li>A PDF's own scripts never run: Leaflet doesn't contain a JavaScript engine for them.</li>
<li>No telemetry, no update checks.</li>
<li>No list of recent files, unless you switch it on in Settings.</li>
<li>Saved files get no author, dates or program name; when a PDF is saved, its author, creator and producer fields and its XMP metadata are removed (the title stays). Deleted pages are really removed from the file.</li></ul>
<p style="color:var(--muted)">Free software (MIT). PDF.js (Apache 2.0) and pdf-lib (MIT) are included.</p>` });
}

async function settings() {
  const html = `
<label class="row">Theme <select id="set-theme">
  <option value="auto">Follow the system</option><option value="light">Light</option>
  <option value="dark">Dark</option><option value="classic">Classic (grey, bevelled)</option></select></label>
<label class="row"><input type="checkbox" id="set-recent"> Remember recently opened files</label>
<small>Off by default. When it's on, the last ${10} files are listed on the start screen
(kept in ~/.config/leaflet/settings.json). Turning it off forgets them.</small>`;
  let themeSel, recentBox;
  const ok = await dialog({ title: 'Settings', html, buttons: [{ label: 'Cancel', value: false }, { label: 'Save', value: true, primary: true }],
    checkbox: (box) => {
      themeSel = box.querySelector('#set-theme');
      recentBox = box.querySelector('#set-recent');
      themeSel.value = state.themeSetting || 'auto';
      recentBox.checked = !!state.rememberRecent;
    } });
  if (ok) post('settings', { theme: themeSel.value, rememberRecent: recentBox.checked });
}

// ------------------------------------------------------------------ find

const findBox = $('#find');
let findTimer = 0;
findBox.addEventListener('input', () => {
  clearTimeout(findTimer);
  findTimer = setTimeout(() => find(false, false), 200);
});
findBox.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); find(e.shiftKey, true); }
  if (e.key === 'Escape') { e.preventDefault(); findBox.value = ''; find(false, false); mode?.focus(); }
});
function find(prev, again) {
  if (!mode) return;
  mode.find(findBox.value, { prev, again });
}

// ------------------------------------------------------------------ keyboard

const inField = (t) => t.closest && (t.closest('input, select, textarea, #modal') !== null);

document.addEventListener('keydown', (e) => {
  if (!$('#modal').hidden) return;
  if (!$('#menu').hidden && e.key === 'Escape') { closeMenu(); e.preventDefault(); return; }
  const ctrl = e.ctrlKey || e.metaKey;
  const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  let handled = true;
  if (ctrl && !e.altKey && k === 'o') post('open');
  else if (ctrl && !e.altKey && k === 'n') post('new');
  else if (ctrl && k === 's') save(e.shiftKey);
  else if (ctrl && k === 'p') { if (mode) printDoc(); }
  else if (ctrl && k === 'w') post('close');
  else if (ctrl && k === 'q') post('quit');
  else if (ctrl && k === 'f') { if (mode) { findBox.focus(); findBox.select(); } }
  else if (e.key === 'F3') find(e.shiftKey, true);
  else if (e.key === 'F9') { if (mode) toggleSidebar(); }
  else if (e.key === 'F11') post('fullscreen');
  else if (e.key === 'F1') shortcuts();
  else handled = false;
  if (!handled && mode && !(inField(e.target) && !e.target.closest('#doc'))) handled = mode.keydown(e);
  if (handled) { e.preventDefault(); e.stopPropagation(); }
}, true);

// Ctrl+wheel zooms
document.addEventListener('wheel', (e) => {
  if ((e.ctrlKey || e.metaKey) && mode) {
    e.preventDefault();
    mode.command(e.deltaY < 0 ? 'zoom-in' : 'zoom-out');
  }
}, { passive: false });

// ------------------------------------------------------------------ dropping files

body.addEventListener('dragover', (e) => {
  if ([...e.dataTransfer.types].includes('Files')) {
    e.preventDefault();
    $('#welcome').classList.add('drop');
  }
});
body.addEventListener('dragleave', (e) => { if (e.target === body || e.target.id === 'welcome') $('#welcome').classList.remove('drop'); });
body.addEventListener('drop', async (e) => {
  $('#welcome').classList.remove('drop');
  if (![...e.dataTransfer.types].includes('Files')) return;
  e.preventDefault();
  const uri = (e.dataTransfer.getData('text/uri-list') || '').split(/\r?\n/).find((l) => l && !l.startsWith('#'));
  if (uri && uri.startsWith('file://')) { post('drop', { uri }); return; }
  const file = e.dataTransfer.files[0];
  if (file) post('drop', { token: await put(await file.arrayBuffer()), name: file.name });
});

// ------------------------------------------------------------------ messages from leaflet.py

function applyState(s) {
  state = { ...state, ...s };
  document.documentElement.dataset.theme = state.theme || 'light';
  const list = $('#recent ul');
  list.innerHTML = '';
  for (const path of state.recent || []) {
    const li = document.createElement('li');
    const b = document.createElement('button');
    const name = path.split('/').pop();
    b.textContent = name;
    const small = document.createElement('small');
    small.textContent = path.slice(0, path.length - name.length - 1);
    b.append(small);
    b.title = path;
    b.addEventListener('click', () => post('open', { path }));
    li.append(b);
    list.append(li);
  }
  $('#recent').hidden = !(state.recent || []).length;
}

async function load(msg) {
  app.banner('');
  findBox.value = '';
  app.findCount('');
  if (mode && mode.kind !== msg.kind) { await mode.close(); mode = null; }
  if (!mode) {
    if (msg.kind === 'pdf') {
      const { PdfMode } = await import('./pdfview.mjs');
      mode = new PdfMode(app);
    } else {
      const { DocMode } = await import('./docedit.mjs');
      mode = new DocMode(app);
    }
  }
  body.classList.remove('empty', 'pdf', 'doc');
  body.classList.add(msg.kind);
  $('#welcome').hidden = true;
  $('#pdf-container').hidden = msg.kind !== 'pdf';
  $('#doc-scroll').hidden = msg.kind !== 'doc';
  $('#thumbs').hidden = msg.kind !== 'pdf';
  $('#outline').hidden = msg.kind !== 'doc';
  dirty = false;
  $('#toolbar .save').classList.remove('dirty');
  try {
    await mode.open(msg);
  } catch (e) {
    await mode.close?.();
    mode = null;
    body.classList.remove('pdf', 'doc');
    body.classList.add('empty');
    $('#welcome').hidden = false;
    $('#pdf-container').hidden = $('#doc-scroll').hidden = true;
    post('title', { dirty: false });
    if (e && e.cancelled) return;
    app.error(`Couldn't open ${msg.name}: ${e && e.message || e}`);
    return;
  }
  toggleSidebar(true);
  app.log(`loaded ${msg.kind}`);
}

const handlers = {
  state: applyState,
  load,
  error: (m) => app.error(m.text),
  saved(m) {
    app.setDirty(false);
    app.status(`Saved ${m.name}`, null);
    mode?.saved?.(m);
    app.log(`saved ${m.format} ${m.name}`);
  },
  async exported(m) {     // a printed document: strip the metadata, then write it
    const ops = await pdfops();
    const clean = await ops.finalize(await take(m.token));
    post('write-export', { token: await put(clean, 'application/pdf'), path: m.path });
  },
  picked: (m) => mode?.picked(m),
  'save-request': () => save(false),
};

window.Leaflet = {
  handle(msg) {
    const fn = handlers[msg.what];
    if (fn) Promise.resolve(fn(msg)).catch((e) => app.error(String(e && e.message || e)));
  },
  get mode() { return mode; },
};

post('ready');
