// The document editor: a page you type on. leaflet.py turns DOCX/ODT/RTF/TXT
// into plain HTML (only the tags it knows), and turns what's here back into
// a file when it's saved. Pasted content is cleaned the same way, so nothing
// that could load from the network gets in.

const $ = (sel) => document.querySelector(sel);
const doc = () => $('#doc');

const ALLOWED = new Set(['P', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'B', 'STRONG', 'I', 'EM', 'U', 'S', 'STRIKE', 'SUP',
  'SUB', 'A', 'BR', 'UL', 'OL', 'LI', 'TABLE', 'TBODY', 'THEAD', 'TR', 'TD', 'TH', 'IMG', 'HR', 'DIV', 'SPAN']);
const DROP = new Set(['SCRIPT', 'STYLE', 'TEMPLATE', 'IFRAME', 'OBJECT', 'EMBED', 'SVG', 'MATH', 'VIDEO', 'AUDIO',
  'LINK', 'META', 'TITLE', 'HEAD', 'NOSCRIPT', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'CANVAS', 'FORM']);

// Copy only known tags and a few safe attributes; everything else becomes its text.
export function clean(html) {
  const src = new DOMParser().parseFromString(html, 'text/html');
  const out = document.createDocumentFragment();
  const walk = (node, into) => {
    for (const child of node.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) { into.append(child.textContent); continue; }
      if (child.nodeType !== Node.ELEMENT_NODE || DROP.has(child.tagName)) continue;
      if (!ALLOWED.has(child.tagName)) { walk(child, into); continue; }
      const el = document.createElement(child.tagName === 'DIV' ? 'p' : child.tagName.toLowerCase());
      if (child.tagName === 'A') {
        const href = child.getAttribute('href') || '';
        if (/^(https?:|mailto:)/i.test(href)) el.setAttribute('href', href);
      } else if (child.tagName === 'IMG') {
        const s = child.getAttribute('src') || '';
        if (!/^data:image\/(png|jpeg|gif|bmp);base64,/i.test(s)) continue;
        el.setAttribute('src', s);
        for (const a of ['width', 'height', 'alt']) if (child.hasAttribute(a)) el.setAttribute(a, child.getAttribute(a));
      } else if (child.tagName === 'TD' || child.tagName === 'TH') {
        if (child.getAttribute('colspan')) el.setAttribute('colspan', child.getAttribute('colspan'));
      } else if (child.tagName === 'HR') {
        if (child.classList.contains('page-break')) el.className = 'page-break'; else continue;
      }
      const align = child.style && child.style.textAlign;
      if (align && /^(center|right|justify)$/.test(align) && /^(P|H\d|DIV|LI|TD|TH)$/.test(child.tagName)) {
        el.style.textAlign = align;
      }
      if (child.tagName === 'SPAN') {
        const st = child.style;
        let inner = el;
        const wrap = (tag) => { const w = document.createElement(tag); inner.append(w); inner = w; };
        if (st.fontWeight === 'bold' || +st.fontWeight >= 600) wrap('b');
        if (st.fontStyle === 'italic') wrap('i');
        if ((st.textDecorationLine || st.textDecoration || '').includes('underline')) wrap('u');
        walk(child, inner);
        into.append(...(el.childNodes.length ? [el] : []));
        continue;
      }
      walk(child, el);
      into.append(el);
    }
  };
  walk(src.body, out);
  const box = document.createElement('div');
  box.append(out);
  return box.innerHTML;
}

const BLOCKS = 'P,H1,H2,H3,H4,H5,H6,LI,TD,TH,DIV';

export class DocMode {
  kind = 'doc';

  constructor(app) {
    this.app = app;
    this.format = 'docx';
    this.name = '';
    this.saveAs = false;
    this.zoom = 1;
    const d = doc();
    document.execCommand('defaultParagraphSeparator', false, 'p');
    document.execCommand('styleWithCSS', false, false);
    d.addEventListener('input', () => this.changed());
    d.addEventListener('paste', (e) => this.paste(e));
    d.addEventListener('drop', (e) => {
      if ([...e.dataTransfer.types].includes('Files')) return;   // a file: app.js opens it
      const html = e.dataTransfer.getData('text/html');
      if (html) { e.preventDefault(); document.execCommand('insertHTML', false, clean(html)); }
    });
    d.addEventListener('click', (e) => {
      const a = e.target.closest('a[href]');
      if (a && (e.ctrlKey || e.metaKey)) { e.preventDefault(); this.app.post('link', { url: a.href }); }
      else if (a) e.preventDefault();
    });
    this.onSel = () => this.selectionChanged();
    document.addEventListener('selectionchange', this.onSel);
    $('#block').addEventListener('change', (e) => { this.block(e.target.value); doc().focus(); });
  }

  async open(msg) {
    this.name = msg.name;
    this.format = msg.format;
    this.saveAs = !!msg.saveAs;
    const d = doc();
    d.innerHTML = msg.html;       // made by leaflet_formats.to_html from the model: known tags only
    d.scrollTop = 0;
    $('#doc-scroll').scrollTop = 0;
    this.app.setDirty(false);
    this.outline();
    this.stats();
    if (msg.warnings && msg.warnings.length) {
      this.app.banner(`Leaflet can't keep everything in this file. If you save it, these will be left out: ${msg.warnings.join(', ')}. Use “Save as” to keep the original.`);
    } else if (msg.format === 'rtf') {
      this.app.banner('RTF files are opened for reading and editing; saving makes a Word (.docx) file.');
    }
    d.focus();
    const r = document.createRange();
    r.setStart(d, 0);
    r.collapse(true);
    getSelection().removeAllRanges();
    getSelection().addRange(r);
  }

  async close() {
    clearTimeout(this.timer);
    document.removeEventListener('selectionchange', this.onSel);
    $('#tablebar').hidden = true;
    doc().innerHTML = '';
    $('#outline').innerHTML = '';
  }

  focus() { doc().focus(); }

  changed() {
    this.app.setDirty(true);
    clearTimeout(this.timer);
    this.timer = setTimeout(() => { this.outline(); this.stats(); }, 400);
  }

  // ---------------------------------------------------------------- saving

  async save(as, format) {
    let fmt = format || this.format;
    if (fmt === 'rtf') fmt = 'docx';
    const token = await this.app.put(doc().innerHTML, 'text/html');
    this.app.post('save', { token, as: !!as || this.saveAs || fmt !== this.format, format: fmt, suggest: this.name });
  }

  saved(m) {
    if (m.export) return;
    this.name = m.name;
    this.format = m.format;
    this.saveAs = false;
    this.app.banner('');
  }

  // ---------------------------------------------------------------- editing

  exec(cmd, value = null) {
    doc().focus();
    document.execCommand(cmd, false, value);
    this.changed();
    this.selectionChanged();
  }

  block(tag) {
    this.exec('formatBlock', `<${tag}>`);
  }

  currentBlock() {
    const sel = getSelection();
    if (!sel.rangeCount) return null;
    let n = sel.anchorNode;
    if (!n || !doc().contains(n)) return null;
    if (n.nodeType !== 1) n = n.parentElement;
    return n.closest(BLOCKS);
  }

  selectionChanged() {
    const b = this.currentBlock();
    if (!b) { $('#tablebar').hidden = true; return; }
    const tag = b.tagName.toLowerCase();
    $('#block').value = /^h[1-3]$/.test(tag) ? tag : 'p';
    for (const c of ['bold', 'italic', 'underline']) {
      document.querySelector(`[data-cmd="${c}"]`).classList.toggle('on', document.queryCommandState(c));
    }
    document.querySelector('[data-cmd="ul"]').classList.toggle('on', document.queryCommandState('insertUnorderedList'));
    document.querySelector('[data-cmd="ol"]').classList.toggle('on', document.queryCommandState('insertOrderedList'));
    this.tableBar(b.closest('td, th'));
  }

  paste(e) {
    const html = e.clipboardData.getData('text/html');
    e.preventDefault();
    if (html) {
      document.execCommand('insertHTML', false, clean(html));
    } else {
      const text = e.clipboardData.getData('text/plain');
      document.execCommand('insertText', false, text);
    }
    this.changed();
  }

  alignMenu(anchor) {
    this.app.menu(anchor, [
      ['align', 'Align left', () => this.exec('justifyLeft'), 'Ctrl+L'],
      ['align-center', 'Centre', () => this.exec('justifyCenter'), 'Ctrl+E'],
      ['align-right', 'Align right', () => this.exec('justifyRight'), 'Ctrl+R'],
      ['align-justify', 'Justify', () => this.exec('justifyFull'), 'Ctrl+J'],
    ]);
  }

  insertTable(rows = 3, cols = 3) {
    const cells = Array.from({ length: cols }, () => '<td><br></td>').join('');
    const html = `<table><tbody>${Array.from({ length: rows }, () => `<tr>${cells}</tr>`).join('')}</tbody></table><p><br></p>`;
    this.exec('insertHTML', html);
  }

  tableBar(cell) {
    const bar = $('#tablebar');
    if (!cell) { bar.hidden = true; this.cell = null; return; }
    this.cell = cell;
    if (!bar.dataset.built) {
      bar.dataset.built = '1';
      const actions = [['Row above', () => this.addRow(false)], ['Row below', () => this.addRow(true)],
        ['Column left', () => this.addCol(false)], ['Column right', () => this.addCol(true)],
        ['Delete row', () => this.delRow()], ['Delete column', () => this.delCol()], ['Delete table', () => this.delTable()]];
      for (const [label, fn] of actions) {
        const b = document.createElement('button');
        b.textContent = label;
        b.addEventListener('mousedown', (e) => e.preventDefault());
        b.addEventListener('click', () => { fn(); this.changed(); });
        bar.append(b);
      }
    }
    const table = cell.closest('table');
    const r = table.getBoundingClientRect();
    bar.hidden = false;
    const top = r.top - bar.offsetHeight - 6;
    bar.style.left = `${Math.max(4, Math.min(r.left, innerWidth - bar.offsetWidth - 4))}px`;
    bar.style.top = `${top < 50 ? r.bottom + 6 : top}px`;
  }

  colIndex(cell) {
    let i = 0;
    for (let c = cell.previousElementSibling; c; c = c.previousElementSibling) i += c.colSpan || 1;
    return i;
  }

  cellAt(row, col) {
    let i = 0;
    for (const c of row.cells) { if (i + (c.colSpan || 1) > col) return c; i += c.colSpan || 1; }
    return null;
  }

  addRow(below) {
    const row = this.cell.parentElement;
    const n = [...row.cells].reduce((s, c) => s + (c.colSpan || 1), 0);
    const tr = document.createElement('tr');
    for (let i = 0; i < n; i++) { const td = document.createElement('td'); td.innerHTML = '<br>'; tr.append(td); }
    row.parentElement.insertBefore(tr, below ? row.nextSibling : row);
  }

  addCol(right) {
    const col = this.colIndex(this.cell) + (right ? (this.cell.colSpan || 1) : 0);
    for (const row of this.cell.closest('table').rows) {
      const td = document.createElement('td');
      td.innerHTML = '<br>';
      const at = this.cellAt(row, col);
      row.insertBefore(td, at && this.colIndex(at) === col ? at : (at ? at.nextSibling : null));
    }
  }

  delRow() {
    const row = this.cell.parentElement;
    const table = row.closest('table');
    row.remove();
    if (!table.rows.length) table.remove();
    $('#tablebar').hidden = true;
  }

  delCol() {
    const col = this.colIndex(this.cell);
    const table = this.cell.closest('table');
    for (const row of [...table.rows]) {
      const c = this.cellAt(row, col);
      if (!c) continue;
      if ((c.colSpan || 1) > 1) c.colSpan -= 1; else c.remove();
      if (!row.cells.length) row.remove();
    }
    if (!table.rows.length) table.remove();
    $('#tablebar').hidden = true;
  }

  delTable() {
    this.cell.closest('table').remove();
    $('#tablebar').hidden = true;
  }

  async picked(m) {
    if (m.kind !== 'image') return;
    const bytes = await this.app.take(m.token);
    const url = await new Promise((resolve) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result);
      r.readAsDataURL(new Blob([bytes], { type: sniff(bytes) }));
    });
    const img = new Image();
    img.src = url;
    await img.decode().catch(() => {});
    let w = img.naturalWidth || 0, h = img.naturalHeight || 0;
    if (w > 600) { h = Math.round(h * 600 / w); w = 600; }
    if (this.range) { getSelection().removeAllRanges(); getSelection().addRange(this.range); }
    this.exec('insertHTML', `<img src="${url}"${w ? ` width="${w}" height="${h}"` : ''} alt="">`);
  }

  // ---------------------------------------------------------------- sidebar outline and status

  outline() {
    const nav = $('#outline');
    nav.innerHTML = '';
    const heads = doc().querySelectorAll('h1, h2, h3, h4, h5, h6');
    if (!heads.length) {
      const p = document.createElement('div');
      p.className = 'empty';
      p.textContent = 'Headings appear here.';
      nav.append(p);
      return;
    }
    for (const h of heads) {
      const b = document.createElement('button');
      b.className = h.tagName.toLowerCase();
      b.textContent = h.textContent.trim() || '(empty heading)';
      b.addEventListener('click', () => {
        h.scrollIntoView({ block: 'start', behavior: 'smooth' });
        const r = document.createRange();
        r.selectNodeContents(h);
        r.collapse(true);
        getSelection().removeAllRanges();
        getSelection().addRange(r);
      });
      nav.append(b);
    }
  }

  stats() {
    const text = doc().innerText || '';
    const words = (text.match(/\S+/g) || []).length;
    const names = { docx: 'Word document', odt: 'OpenDocument text', rtf: 'Rich Text', txt: 'Plain text' };
    this.app.status(`${names[this.format] || 'Document'} · ${words} word${words === 1 ? '' : 's'}`,
      this.zoom !== 1 ? `${Math.round(this.zoom * 100)}%` : '');
  }

  // ---------------------------------------------------------------- find

  find(query, { prev }) {
    if (!query) { this.app.findCount(''); return; }
    const text = (doc().innerText || '').toLowerCase();
    const q = query.toLowerCase();
    let count = 0;
    for (let i = text.indexOf(q); i >= 0; i = text.indexOf(q, i + q.length)) count++;
    if (!count) { this.app.findCount('No matches', true); return; }
    // window.find searches from the selection; keep it inside the page.
    const sel = getSelection();
    if (!sel.rangeCount || !doc().contains(sel.anchorNode)) {
      const r = document.createRange();
      r.setStart(doc(), 0);
      r.collapse(true);
      sel.removeAllRanges();
      sel.addRange(r);
    }
    let found = window.find(query, false, !!prev, true, false, false, false);
    if (found && !doc().contains(getSelection().anchorNode)) found = window.find(query, false, !!prev, true, false, false, false);
    this.app.findCount(`${count} match${count === 1 ? '' : 'es'}`);
    const node = getSelection().anchorNode;
    (node && node.parentElement)?.scrollIntoView({ block: 'center' });
  }

  // ---------------------------------------------------------------- commands and keys

  setZoom(z) {
    this.zoom = Math.max(0.5, Math.min(3, Math.round(z * 10) / 10));
    doc().style.setProperty('--doc-zoom', this.zoom);
    doc().classList.toggle('zoomed', this.zoom !== 1);
    this.stats();
  }

  async command(cmd, anchor) {
    switch (cmd) {
      case 'bold': case 'italic': case 'underline': this.exec(cmd); break;
      case 'ul': this.exec('insertUnorderedList'); break;
      case 'ol': this.exec('insertOrderedList'); break;
      case 'align': this.alignMenu(anchor); break;
      case 'table': this.insertTable(); break;
      case 'image': {
        const sel = getSelection();
        this.range = sel.rangeCount && doc().contains(sel.anchorNode) ? sel.getRangeAt(0).cloneRange() : null;
        this.app.post('pick', { for: 'image' });
        break;
      }
      case 'undo': this.exec('undo'); break;
      case 'redo': this.exec('redo'); break;
      case 'zoom-in': this.setZoom(this.zoom + 0.1); break;
      case 'zoom-out': this.setZoom(this.zoom - 0.1); break;
      case 'export-pdf': this.app.post('export-pdf'); break;
      default: break;
    }
  }

  keydown(e) {
    const ctrl = e.ctrlKey || e.metaKey;
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    const inDoc = e.target.closest && e.target.closest('#doc');
    if (ctrl && (k === '=' || k === '+')) this.setZoom(this.zoom + 0.1);
    else if (ctrl && k === '-') this.setZoom(this.zoom - 0.1);
    else if (ctrl && k === '0' && !inDoc) this.setZoom(1);
    else if (ctrl && e.shiftKey && k === 'e') this.app.post('export-pdf');
    else if (!inDoc) return false;
    else if (ctrl && !e.shiftKey && ['b', 'i', 'u'].includes(k)) this.exec({ b: 'bold', i: 'italic', u: 'underline' }[k]);
    else if (ctrl && ['0', '1', '2', '3'].includes(k)) this.block(k === '0' ? 'p' : `h${k}`);
    else if (ctrl && e.shiftKey && (k === '8' || k === '*')) this.exec('insertUnorderedList');
    else if (ctrl && e.shiftKey && (k === '7' || k === '&')) this.exec('insertOrderedList');
    else if (ctrl && !e.shiftKey && k === 'l') this.exec('justifyLeft');
    else if (ctrl && !e.shiftKey && k === 'e') this.exec('justifyCenter');
    else if (ctrl && !e.shiftKey && k === 'r') this.exec('justifyRight');
    else if (ctrl && !e.shiftKey && k === 'j') this.exec('justifyFull');
    else if (ctrl && k === 'z') this.exec(e.shiftKey ? 'redo' : 'undo');
    else if (ctrl && k === 'y') this.exec('redo');
    else if (ctrl && k === 'Enter') this.exec('insertHTML', '<hr class="page-break"><p><br></p>');
    else if (k === 'Tab' && !ctrl) {
      const li = this.currentBlock()?.closest('li');
      if (li) this.exec(e.shiftKey ? 'outdent' : 'indent');
      else if (this.currentBlock()?.closest('td, th')) this.nextCell(e.shiftKey);
      else if (!e.shiftKey) this.exec('insertText', '\t');
    } else return false;
    return true;
  }

  nextCell(back) {
    const cells = [...this.cell.closest('table').querySelectorAll('td, th')];
    const i = cells.indexOf(this.cell) + (back ? -1 : 1);
    if (i < 0) return;
    if (i >= cells.length) { this.addRow(true); this.changed(); return this.nextCell(false); }
    const r = document.createRange();
    r.selectNodeContents(cells[i]);
    r.collapse(true);
    getSelection().removeAllRanges();
    getSelection().addRange(r);
    return undefined;
  }
}

function sniff(b) {
  if (b[0] === 0x89 && b[1] === 0x50) return 'image/png';
  if (b[0] === 0xff && b[1] === 0xd8) return 'image/jpeg';
  if (b[0] === 0x47 && b[1] === 0x49) return 'image/gif';
  if (b[0] === 0x42 && b[1] === 0x4d) return 'image/bmp';
  return 'application/octet-stream';
}
