// The PDF view: PDF.js for showing, searching, forms and annotations;
// pdf-lib (through pdfops.mjs) for turning, deleting, moving and merging pages.
//
// PDF.js runs here without its scripting support: there's no
// PDFScriptingManager and the JavaScript engine it would use (quickjs) isn't
// shipped, so a PDF's scripts are never run. The PDF's bytes come from
// leaflet.py through the leaflet:// scheme; nothing is fetched from anywhere.

const V = 'vendor/pdfjs/';
let pdfjs = null;
let viewerLib = null;

async function libs() {
  if (!pdfjs) {
    pdfjs = await import(`./${V}pdf.min.mjs`);
    globalThis.pdfjsLib = pdfjs;
    pdfjs.GlobalWorkerOptions.workerSrc = `${V}pdf.worker.min.mjs`;
    viewerLib = await import(`./${V}pdf_viewer.mjs`);
  }
}

const $ = (sel) => document.querySelector(sel);
const ZOOMS = [0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5];
const MAX_UNDO = 6;
const THUMB_W = 120;

class Cancelled extends Error { constructor() { super('cancelled'); this.cancelled = true; } }

export class PdfMode {
  kind = 'pdf';

  constructor(app) {
    this.app = app;
    this.doc = null;
    this.bytes = null;           // the file as last loaded (before unsaved annotations)
    this.name = '';
    this.encrypted = false;
    this.undo = [];
    this.selected = new Set();
    this.tool = 'select';
    this.thumbObserver = null;
    this.ready = libs().then(() => this.setup());
  }

  setup() {
    const { EventBus, PDFLinkService, PDFFindController, PDFViewer, LinkTarget } = viewerLib;
    const container = $('#pdf-container');
    this.eventBus = new EventBus();
    this.linkService = new PDFLinkService({ eventBus: this.eventBus, externalLinkTarget: LinkTarget.BLANK,
      externalLinkRel: 'noopener noreferrer nofollow' });
    this.findController = new PDFFindController({ eventBus: this.eventBus, linkService: this.linkService });
    this.viewer = new PDFViewer({
      container, viewer: $('#viewer'), eventBus: this.eventBus, linkService: this.linkService,
      findController: this.findController, imageResourcesPath: `${V}images/`,
      annotationMode: pdfjs.AnnotationMode.ENABLE_FORMS,
      annotationEditorMode: pdfjs.AnnotationEditorType.NONE,
      annotationEditorHighlightColors: 'yellow=#FFFF98,green=#53FFBC,blue=#80EBFF,pink=#FFCBE6,red=#FF4F5F',
      maxCanvasPixels: 16 * 1024 * 1024, enableHWA: false,
    });
    this.linkService.setViewer(this.viewer);

    this.eventBus.on('pagesinit', () => {
      this.viewer.currentScaleValue = this.restoreScale || 'page-width';
      if (this.restorePage) this.viewer.currentPageNumber = Math.min(this.restorePage, this.doc.numPages);
      this.restorePage = this.restoreScale = null;
    });
    this.eventBus.on('pagechanging', ({ pageNumber }) => this.pageChanged(pageNumber));
    this.eventBus.on('scalechanging', ({ scale, presetValue }) => this.scaleChanged(scale, presetValue));
    this.eventBus.on('updatefindmatchescount', ({ matchesCount }) => this.findResult(matchesCount));
    this.eventBus.on('updatefindcontrolstate', ({ state, matchesCount }) => {
      this.findResult(matchesCount, state === viewerLib.FindState.NOT_FOUND);
    });

    // Links leave only through leaflet.py, which asks first.
    container.addEventListener('click', (e) => {
      const a = e.target.closest && e.target.closest('a[href]');
      if (!a) return;
      const href = a.getAttribute('href') || '';
      if (/^(https?:|mailto:)/i.test(href)) {
        e.preventDefault();
        e.stopPropagation();
        this.app.post('link', { url: a.href });
      } else if (!href.startsWith('#')) {
        e.preventDefault();
      }
    }, true);

    const pageBox = $('#page');
    pageBox.addEventListener('change', () => this.goTo(parseInt(pageBox.value, 10)));
    pageBox.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { this.goTo(parseInt(pageBox.value, 10)); $('#pdf-container').focus(); }
    });
    $('#zoom').addEventListener('change', (e) => {
      const v = e.target.value;
      this.viewer.currentScaleValue = v;
    });

    this.setupThumbs();
  }

  // ---------------------------------------------------------------- opening

  async open(msg) {
    await this.ready;
    this.name = msg.name;
    const bytes = await this.app.take(msg.token);
    this.undo = [];
    await this.load(bytes);
    this.app.setDirty(false);
  }

  async load(bytes, keep = false) {
    const { getDocument } = pdfjs;
    if (keep && this.doc) {
      this.restorePage = this.viewer.currentPageNumber;
      this.restoreScale = this.viewer.currentScaleValue;
    }
    const oldTask = this.task;
    const task = getDocument({
      data: bytes.slice(),            // PDF.js takes over the buffer it's given
      isEvalSupported: false, enableXfa: false, useSystemFonts: true, stopAtErrors: false,
      cMapUrl: `${V}cmaps/`, cMapPacked: true, standardFontDataUrl: `${V}standard_fonts/`,
      wasmUrl: `${V}wasm/`, iccUrl: `${V}iccs/`,
      password: keep ? this.password : undefined,
    });
    let asked = false;
    task.onPassword = async (update, reason) => {
      const wrong = reason === pdfjs.PasswordResponses.INCORRECT_PASSWORD;
      const pw = await this.app.dialog({
        title: 'This PDF is protected',
        text: wrong ? 'That password isn\'t right. Try again:' : `Enter the password to open ${this.name}:`,
        input: { type: 'password', label: 'Password' },
        buttons: [{ label: 'Cancel', value: null }, { label: 'Open', value: true, primary: true }],
      });
      if (pw === null) { task.destroy(); return; }
      asked = true;
      this.password = pw;
      update(pw);
    };
    let doc;
    try {
      doc = await task.promise;
      this.task = task;
    } catch (e) {
      if (task.destroyed || /destroy|Worker was terminated|Loading aborted/i.test(String(e))) throw new Cancelled();
      throw new Error(e && e.name === 'InvalidPDFException' ? 'the file isn\'t a valid PDF' : (e.message || String(e)));
    }
    this.doc = doc;
    this.bytes = bytes;
    let info = {};
    try { info = (await doc.getMetadata()).info || {}; } catch { /* no metadata */ }
    this.encrypted = asked || !!info.EncryptFilterName || !!info.IsEncrypted;
    doc.annotationStorage.onSetModified = () => this.app.setDirty(true);
    this.selected = new Set();
    this.setTool('select');
    this.viewer.setDocument(doc);
    this.linkService.setDocument(doc, null);
    if (oldTask) oldTask.destroy();
    $('#pages').textContent = `/ ${doc.numPages}`;
    this.buildThumbs();
    this.updateButtons();
    this.app.status(`PDF · ${doc.numPages} page${doc.numPages === 1 ? '' : 's'}${this.encrypted ? ' · encrypted' : ''}`, null);
    if (info.IsXFAPresent) this.app.banner('This PDF has an XFA form, which Leaflet shows but can\'t fill in.');
    else if (this.encrypted) this.app.banner('This PDF is encrypted. You can read, fill in and annotate it; turning, deleting or adding pages needs an unencrypted copy.');
  }

  async close() {
    if (this.task) await this.task.destroy();
    this.doc = this.task = null;
    this.viewer.setDocument(null);
    this.linkService.setDocument(null);
    $('#thumbs').innerHTML = '';
  }

  // ---------------------------------------------------------------- the file as it is now

  async currentBytes() {
    if (this.doc.annotationStorage.size > 0) return new Uint8Array(await this.doc.saveDocument());
    return this.bytes.slice();
  }

  async save(as) {
    this.app.busy(true);
    try {
      let bytes = await this.currentBytes();
      if (!this.encrypted) bytes = await (await this.app.pdfops()).finalize(bytes);
      const token = await this.app.put(bytes, 'application/pdf');
      this.app.post('save', { token, as: !!as, format: 'pdf', suggest: this.name });
    } finally {
      this.app.busy(false);
    }
  }

  // Printing sends the PDF itself (with any unsaved notes and form values)
  // to the system's print dialog.
  async print() {
    this.app.busy(true);
    try {
      const token = await this.app.put(await this.currentBytes(), 'application/pdf');
      this.app.post('print', { token });
    } finally {
      this.app.busy(false);
    }
  }

  saved(m) {
    this.name = m.name;
    this.doc?.annotationStorage.resetModified();
  }

  // ---------------------------------------------------------------- page operations

  targets() {
    const sel = [...this.selected].sort((a, b) => a - b);
    return sel.length ? sel : [this.viewer.currentPageNumber - 1];
  }

  async pageOp(label, fn, selectAfter) {
    if (this.encrypted) {
      this.app.error('This PDF is encrypted, so Leaflet can\'t change its pages. (Reading, forms and annotations still work.)');
      return;
    }
    this.app.busy(true);
    try {
      const ops = await this.app.pdfops();
      const before = await this.currentBytes();
      const after = await fn(ops, before);
      this.undo.push({ bytes: before, label });
      if (this.undo.length > MAX_UNDO) this.undo.shift();
      await this.load(after, true);
      if (selectAfter) this.select(selectAfter, true);
      this.app.setDirty(true);
      this.app.status(null, `${label}`);
      this.app.log(`page op ${label}`);
    } finally {
      this.app.busy(false);
    }
  }

  rotate(by) {
    const pages = this.targets();
    return this.pageOp(by > 0 ? 'Turned right' : 'Turned left', (ops, b) => ops.rotate(b, pages, by), pages);
  }

  async deletePages() {
    const pages = this.targets();
    if (pages.length >= this.doc.numPages) { this.app.error('A PDF needs at least one page.'); return; }
    const what = pages.length === 1 ? `page ${pages[0] + 1}` : `${pages.length} pages`;
    const ok = await this.app.dialog({ title: 'Delete pages', text: `Delete ${what}? You can undo this until you close the file.`,
      buttons: [{ label: 'Cancel', value: false }, { label: 'Delete', value: true, primary: true }] });
    if (ok) await this.pageOp(`Deleted ${what}`, (ops, b) => ops.remove(b, pages));
  }

  async moveBy(delta) {
    const pages = this.targets();
    const n = this.doc.numPages;
    const to = delta < 0 ? Math.max(0, pages[0] - 1) : Math.min(n, pages[pages.length - 1] + 2);
    if ((delta < 0 && pages[0] === 0) || (delta > 0 && pages[pages.length - 1] === n - 1)) return;
    const ops = await this.app.pdfops();
    const order = ops.moveOrder(n, pages, to);
    const newPos = pages.map((p) => order.indexOf(p));
    await this.pageOp('Moved', (o, b) => o.reorder(b, order), newPos);
    this.goTo(newPos[0] + 1);
  }

  async undoPages() {
    const last = this.undo.pop();
    if (!last) return;
    await this.load(last.bytes, true);
    this.app.setDirty(true);
    this.app.status(null, `Undid: ${last.label}`);
    this.updateButtons();
  }

  picked(m) {
    if (m.kind !== 'pdf') return;
    const at = m.at != null ? m.at : this.viewer.currentPageNumber;
    return this.app.take(m.token).then((other) =>
      this.pageOp(`Inserted ${m.name}`, (ops, b) => ops.merge(b, other, at)));
  }

  // ---------------------------------------------------------------- tools, zoom, pages

  setTool(name) {
    const T = pdfjs.AnnotationEditorType;
    const modes = { select: T.NONE, highlight: T.HIGHLIGHT, note: T.FREETEXT, draw: T.INK };
    this.tool = name;
    for (const t of Object.keys(modes)) document.querySelector(`[data-cmd="tool-${t}"]`)?.classList.toggle('on', t === name);
    $('#viewer').className = `pdfViewer tool-${name}`;
    if (this.doc) {
      try { this.viewer.annotationEditorMode = { mode: modes[name] }; } catch (e) { this.app.log(`tool: ${e}`); }
    }
  }

  goTo(n) {
    if (!this.doc || !Number.isFinite(n)) return;
    this.viewer.currentPageNumber = Math.max(1, Math.min(n, this.doc.numPages));
  }

  zoom(dir) {
    const cur = this.viewer.currentScale;
    const next = dir > 0 ? ZOOMS.find((z) => z > cur + 0.01) : [...ZOOMS].reverse().find((z) => z < cur - 0.01);
    if (next) this.viewer.currentScale = next;
  }

  pageChanged(n) {
    $('#page').value = n;
    const total = this.doc ? this.doc.numPages : 0;
    this.app.status(null, `Page ${n} of ${total} · ${Math.round(this.viewer.currentScale * 100)}%`);
    for (const el of $('#thumbs').querySelectorAll('.thumb.current')) el.classList.remove('current');
    const t = $('#thumbs').children[n - 1];
    if (t) {
      t.classList.add('current');
      if (!this.app.sidebarShown()) return;
      const sb = $('#sidebar');
      const r = t.getBoundingClientRect(), s = sb.getBoundingClientRect();
      if (r.top < s.top || r.bottom > s.bottom) t.scrollIntoView({ block: 'nearest' });
    }
    this.updateButtons();
  }

  scaleChanged(scale, preset) {
    const sel = $('#zoom');
    const known = [...sel.options].find((o) => o.value === String(preset) || (!preset && Math.abs(parseFloat(o.value) - scale) < 0.001));
    if (known) sel.value = known.value;
    else {
      const custom = sel.querySelector('[value="custom"]');
      custom.textContent = `${Math.round(scale * 100)}%`;
      sel.value = 'custom';
    }
    if (this.doc) this.app.status(null, `Page ${this.viewer.currentPageNumber} of ${this.doc.numPages} · ${Math.round(scale * 100)}%`);
  }

  updateButtons() {
    const undo = document.querySelector('[data-cmd="undo-pages"]');
    undo.disabled = !this.undo.length;
    undo.title = this.undo.length ? `Undo: ${this.undo.at(-1).label}` : 'Undo the last page change';
    for (const c of ['rotate', 'delete-pages', 'insert-pdf']) document.querySelector(`[data-cmd="${c}"]`).disabled = this.encrypted;
    document.querySelector('[data-cmd="prev"]').disabled = this.viewer.currentPageNumber <= 1;
    document.querySelector('[data-cmd="next"]').disabled = !this.doc || this.viewer.currentPageNumber >= this.doc.numPages;
  }

  // ---------------------------------------------------------------- find

  find(query, { prev, again }) {
    if (!query) {
      this.eventBus.dispatch('find', { source: this, type: '', query: '', highlightAll: true });
      this.app.findCount('');
      return;
    }
    this.eventBus.dispatch('find', { source: this, type: again ? 'again' : '', query, caseSensitive: false,
      entireWord: false, highlightAll: true, findPrevious: !!prev, matchDiacritics: false });
  }

  findResult(count, notFound) {
    if (!count) return;
    if (notFound || count.total === 0) this.app.findCount($('#find').value ? 'No matches' : '', !!$('#find').value);
    else this.app.findCount(`${count.current} of ${count.total}`);
  }

  // ---------------------------------------------------------------- thumbnails

  setupThumbs() {
    const box = $('#thumbs');
    this.thumbObserver = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) this.renderThumb(e.target);
    }, { root: $('#sidebar'), rootMargin: '300px 0px' });

    box.addEventListener('click', (e) => {
      const t = e.target.closest('.thumb');
      if (!t) return;
      const i = +t.dataset.i;
      if (e.shiftKey && this.anchor != null) {
        const [a, b] = [Math.min(this.anchor, i), Math.max(this.anchor, i)];
        this.select(Array.from({ length: b - a + 1 }, (_, k) => a + k), !(e.ctrlKey || e.metaKey));
      } else if (e.ctrlKey || e.metaKey) {
        if (this.selected.has(i)) this.selected.delete(i); else this.selected.add(i);
        this.anchor = i;
        this.paintSelection();
      } else {
        this.anchor = i;
        this.select([i], true);
        this.goTo(i + 1);
      }
      box.focus({ preventScroll: true });
    });
    box.addEventListener('contextmenu', (e) => {
      const t = e.target.closest('.thumb');
      if (!t) return;
      e.preventDefault();
      const i = +t.dataset.i;
      if (!this.selected.has(i)) { this.anchor = i; this.select([i], true); }
      const n = this.selected.size;
      const what = n > 1 ? `${n} pages` : 'page';
      this.app.menu({ x: e.clientX, y: e.clientY }, [
        ['rotate', `Turn ${what} right`, () => this.rotate(90), 'Ctrl+R'],
        ['rotate-left', `Turn ${what} left`, () => this.rotate(-90), 'Ctrl+Shift+R'],
        ['delete-pages', `Delete ${what}…`, () => this.deletePages(), 'Delete'], null,
        ['prev', 'Move to the start', () => this.moveTo(0)],
        ['next', 'Move to the end', () => this.moveTo(this.doc.numPages)], null,
        ['insert-pdf', 'Insert a PDF after this page…', () => this.app.post('pick', { for: 'pdf', at: Math.max(...this.selected) + 1 })],
      ]);
    });
    // Dragging pages to move them.
    box.addEventListener('dragstart', (e) => {
      const t = e.target.closest('.thumb');
      if (!t) return;
      const i = +t.dataset.i;
      if (!this.selected.has(i)) this.select([i], true);
      this.dragging = [...this.selected];
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('application/x-leaflet-pages', this.dragging.join(','));
    });
    box.addEventListener('dragover', (e) => {
      if (!this.dragging) return;
      e.preventDefault();
      e.stopPropagation();
      const t = e.target.closest('.thumb');
      for (const el of box.querySelectorAll('.drop-before, .drop-after')) el.classList.remove('drop-before', 'drop-after');
      if (!t) return;
      const r = t.getBoundingClientRect();
      t.classList.add(e.clientY < r.top + r.height / 2 ? 'drop-before' : 'drop-after');
    });
    box.addEventListener('drop', (e) => {
      if (!this.dragging) return;
      e.preventDefault();
      e.stopPropagation();
      const t = box.querySelector('.drop-before, .drop-after');
      const pages = this.dragging;
      this.dragging = null;
      for (const el of box.querySelectorAll('.drop-before, .drop-after')) el.classList.remove('drop-before', 'drop-after');
      if (!t) return;
      const to = +t.dataset.i + (t.classList.contains('drop-after') ? 1 : 0);
      this.moveTo(to, pages);
    });
    box.addEventListener('dragend', () => { this.dragging = null; });
    box.addEventListener('keydown', (e) => {
      if (!this.doc) return;
      const cur = this.viewer.currentPageNumber - 1;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (e.altKey) { this.moveBy(e.key === 'ArrowUp' ? -1 : 1); e.preventDefault(); e.stopPropagation(); return; }
        const i = Math.max(0, Math.min(this.doc.numPages - 1, cur + (e.key === 'ArrowDown' ? 1 : -1)));
        if (e.shiftKey) { this.selected.add(i); this.paintSelection(); } else { this.anchor = i; this.select([i], true); }
        this.goTo(i + 1);
        e.preventDefault();
        e.stopPropagation();
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        this.deletePages();
        e.preventDefault();
        e.stopPropagation();
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') {
        this.select(Array.from({ length: this.doc.numPages }, (_, k) => k), true);
        e.preventDefault();
        e.stopPropagation();
      }
    });
  }

  async moveTo(to, pages = this.targets()) {
    const n = this.doc.numPages;
    const ops = await this.app.pdfops();
    const order = ops.moveOrder(n, pages, to);
    if (order.every((v, k) => v === k)) return;
    const newPos = pages.map((p) => order.indexOf(p)).sort((a, b) => a - b);
    await this.pageOp(pages.length > 1 ? `Moved ${pages.length} pages` : 'Moved a page', (o, b) => o.reorder(b, order), newPos);
    this.goTo(newPos[0] + 1);
  }

  select(indices, replace) {
    if (replace) this.selected = new Set(indices);
    else for (const i of indices) this.selected.add(i);
    this.paintSelection();
  }

  paintSelection() {
    for (const t of $('#thumbs').children) {
      const on = this.selected.has(+t.dataset.i);
      t.classList.toggle('selected', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    }
  }

  async buildThumbs() {
    const box = $('#thumbs');
    this.thumbObserver.disconnect();
    box.innerHTML = '';
    const doc = this.doc;
    const first = await doc.getPage(1);
    const vp = first.getViewport({ scale: 1 });
    const defaultH = Math.round(THUMB_W * vp.height / vp.width);
    const frag = document.createDocumentFragment();
    for (let i = 0; i < doc.numPages; i++) {
      const t = document.createElement('div');
      t.className = 'thumb';
      t.dataset.i = i;
      t.draggable = true;
      t.setAttribute('role', 'option');
      t.innerHTML = `<div class="frame" style="width:${THUMB_W}px;height:${defaultH}px"></div><span>${i + 1}</span>`;
      frag.append(t);
    }
    box.append(frag);
    for (const t of box.children) this.thumbObserver.observe(t);
    this.paintSelection();
    this.pageChanged(this.viewer.currentPageNumber || 1);
  }

  async renderThumb(t) {
    if (t.dataset.done || !this.doc) return;
    t.dataset.done = '1';
    const doc = this.doc;
    try {
      const page = await doc.getPage(+t.dataset.i + 1);
      if (doc !== this.doc) return;
      const vp1 = page.getViewport({ scale: 1 });
      const scale = THUMB_W / vp1.width;
      const vp = page.getViewport({ scale: scale * Math.min(2, devicePixelRatio || 1) });
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(vp.width);
      canvas.height = Math.round(vp.height);
      const frame = t.querySelector('.frame');
      frame.style.height = `${Math.round(THUMB_W * vp1.height / vp1.width)}px`;
      await page.render({ canvas, canvasContext: canvas.getContext('2d'), viewport: vp,
        annotationMode: pdfjs.AnnotationMode.ENABLE_FORMS }).promise;
      frame.replaceChildren(canvas);
    } catch (e) {
      this.app.log(`thumb ${t.dataset.i}: ${e}`);
    }
  }

  sidebarChanged(shown) {
    if (shown) this.pageChanged(this.viewer.currentPageNumber);
  }

  // ---------------------------------------------------------------- commands and keys

  async command(cmd, anchor) {
    if (!this.doc) return;
    switch (cmd) {
      case 'prev': this.viewer.previousPage(); break;
      case 'next': this.viewer.nextPage(); break;
      case 'zoom-in': this.zoom(1); break;
      case 'zoom-out': this.zoom(-1); break;
      case 'rotate': await this.rotate(90); break;
      case 'rotate-left': await this.rotate(-90); break;
      case 'delete-pages': await this.deletePages(); break;
      case 'insert-pdf': this.app.post('pick', { for: 'pdf', at: this.viewer.currentPageNumber }); break;
      case 'undo-pages': await this.undoPages(); break;
      case 'tool-select': this.setTool('select'); break;
      case 'tool-highlight': this.setTool('highlight'); break;
      case 'tool-note': this.setTool('note'); break;
      case 'tool-draw': this.setTool('draw'); break;
      default: break;
    }
  }

  keydown(e) {
    const ctrl = e.ctrlKey || e.metaKey;
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    const editing = e.target.closest && e.target.closest('input, textarea, [contenteditable="true"], .annotationEditorLayer .freeTextEditor');
    if (ctrl && (k === '=' || k === '+')) this.zoom(1);
    else if (ctrl && k === '-') this.zoom(-1);
    else if (ctrl && k === '0') this.viewer.currentScaleValue = 'page-width';
    else if (ctrl && k === 'g') { $('#page').focus(); $('#page').select(); }
    else if (ctrl && k === 'r') this.rotate(e.shiftKey ? -90 : 90);
    else if (editing) return false;
    else if (k === 'Escape' && this.tool !== 'select') this.setTool('select');
    else if (k === 'Home' && !ctrl) this.goTo(1);
    else if (k === 'End' && !ctrl) this.goTo(this.doc.numPages);
    else if (!ctrl && !e.altKey && k === 'h') this.setTool('highlight');
    else if (!ctrl && !e.altKey && k === 'n') this.setTool('note');
    else if (!ctrl && !e.altKey && k === 'd') this.setTool('draw');
    else return false;
    return true;
  }

  focus() { $('#pdf-container').focus(); }
}
