// Page operations on PDF files, with pdf-lib (globalThis.PDFLib).
//
// Every function takes the file's bytes and returns new bytes; nothing is
// kept between calls. The same module runs in Leaflet's window and in the
// tests (node tests/test_pdfops.mjs).

const lib = () => {
  const L = globalThis.PDFLib;
  if (!L) throw new Error('pdf-lib is not loaded');
  return L;
};

// Never let pdf-lib stamp its own name or the time into the file.
const LOAD = { updateMetadata: false, throwOnInvalidObject: false };
const SAVE = { useObjectStreams: true, addDefaultPage: false, updateFieldAppearances: false };

export class EncryptedError extends Error {}

async function load(bytes) {
  const { PDFDocument, EncryptedPDFError } = lib();
  try {
    return await PDFDocument.load(bytes, LOAD);
  } catch (e) {
    if (e instanceof EncryptedPDFError || /encrypt/i.test(String(e && e.message))) {
      throw new EncryptedError('This PDF is encrypted; Leaflet can view and annotate it, but not change its pages.');
    }
    throw e;
  }
}

async function save(doc, removed) {
  sweep(doc, removed);
  return await doc.save(SAVE);
}

export async function pageCount(bytes) {
  return (await load(bytes)).getPageCount();
}

// Turn pages by a multiple of 90 degrees (clockwise).
export async function rotate(bytes, indices, by) {
  const { degrees } = lib();
  const doc = await load(bytes);
  for (const i of indices) {
    const page = doc.getPage(i);
    const now = page.getRotation().angle || 0;
    page.setRotation(degrees((((now + by) % 360) + 360) % 360));
  }
  return save(doc);
}

export async function remove(bytes, indices) {
  const doc = await load(bytes);
  if (indices.length >= doc.getPageCount()) throw new Error('A PDF needs at least one page.');
  const removed = [];
  for (const i of [...new Set(indices)].sort((a, b) => b - a)) {
    removed.push(doc.getPage(i).ref);
    doc.removePage(i);
  }
  return save(doc, removed);
}

// Put the pages in a new order: order[k] is the old index of the page that
// goes at position k. The page objects stay the same, so links, form fields
// and annotations stay attached to them.
export async function reorder(bytes, order) {
  const doc = await load(bytes);
  const n = doc.getPageCount();
  if (order.length !== n || new Set(order).size !== n || order.some((i) => i < 0 || i >= n)) {
    throw new Error('not a reordering of the pages');
  }
  const pages = doc.getPages();
  for (let i = n - 1; i >= 0; i--) doc.removePage(i);
  order.forEach((old) => doc.addPage(pages[old]));
  return save(doc);
}

// Move the pages `indices` so they start at position `to` (counted in the
// document as it was before the move).
export function moveOrder(n, indices, to) {
  const moving = [...new Set(indices)].sort((a, b) => a - b);
  const rest = [];
  for (let i = 0; i < n; i++) if (!moving.includes(i)) rest.push(i);
  const before = moving.filter((i) => i < to).length;
  const at = Math.max(0, Math.min(rest.length, to - before));
  return [...rest.slice(0, at), ...moving, ...rest.slice(at)];
}

// Insert all pages of `other` at position `at` (default: the end).
export async function merge(bytes, otherBytes, at) {
  const { PDFName, PDFArray, PDFDict } = lib();
  const doc = await load(bytes);
  const other = await load(otherBytes);
  const copied = await doc.copyPages(other, other.getPageIndices());
  let pos = at == null ? doc.getPageCount() : Math.max(0, Math.min(at, doc.getPageCount()));
  for (const page of copied) doc.insertPage(pos++, page);

  // Form fields on the copied pages have to be listed in this document's form.
  const roots = new Set();
  for (const page of copied) {
    const annots = page.node.lookupMaybe(PDFName.of('Annots'), PDFArray);
    if (!annots) continue;
    for (let i = 0; i < annots.size(); i++) {
      const ref = annots.get(i);
      let dict = doc.context.lookup(ref);
      if (!(dict instanceof PDFDict)) continue;
      let top = ref;
      for (let hops = 0; hops < 32; hops++) {
        const parent = dict.get(PDFName.of('Parent'));
        if (!parent) break;
        const pd = doc.context.lookup(parent);
        if (!(pd instanceof PDFDict)) break;
        top = parent;
        dict = pd;
      }
      if (dict.get(PDFName.of('FT')) || dict.get(PDFName.of('Kids'))) roots.add(top);
    }
  }
  if (roots.size) {
    const form = doc.getForm();       // creates /AcroForm when there is none
    const fields = form.acroForm.dict.lookup(PDFName.of('Fields'), PDFArray);
    const have = new Set(fields.asArray().map(String));
    for (const r of roots) if (!have.has(String(r))) fields.push(r);
  }
  return save(doc);
}

// What goes into a file when it's saved: no author, program names, dates
// or XMP packet. The title stays (it's what viewers show for the document).
// Notes and other annotations lose who made them and when; pictures lose
// their own metadata (XMP, and the EXIF, IPTC and comments inside a JPEG).
export async function finalize(bytes) {
  const { PDFName, PDFDict, PDFArray, PDFString, PDFHexString, PDFStream, PDFRawStream, PDFRef } = lib();
  const N = (name) => PDFName.of(name);
  const doc = await load(bytes);
  const ctx = doc.context;
  const info = ctx.lookup(ctx.trailerInfo.Info);
  let title = null;
  if (info instanceof PDFDict) {
    const t = info.get(N('Title'));
    if (t instanceof PDFString || t instanceof PDFHexString) title = t;
  }
  if (title) {
    ctx.trailerInfo.Info = ctx.register(ctx.obj({ Title: title }));
  } else {
    delete ctx.trailerInfo.Info;
  }
  delete ctx.trailerInfo.ID;

  // Annotations: the time they were made or changed, the name of whoever
  // made them (/T) and the identifier their program gave them. A form
  // field's /T is its name, and stays.
  const anonymous = (a) => {
    for (const name of ['M', 'CreationDate', 'NM']) a.delete(N(name));
    if (a.get(N('Subtype')) !== N('Widget') && !a.get(N('FT'))) a.delete(N('T'));
  };
  for (const page of doc.getPages()) {
    const annots = page.node.lookupMaybe(N('Annots'), PDFArray);
    for (let i = 0; annots && i < annots.size(); i++) {
      const a = ctx.lookup(annots.get(i));
      if (!(a instanceof PDFDict)) continue;
      anonymous(a);
      const popup = a.lookupMaybe(N('Popup'), PDFDict);
      if (popup) anonymous(popup);
    }
  }

  // Everything in the file: the XMP packets and the programs' private data
  // that hang on the document, its pages, pictures and fonts.
  for (const [ref, obj] of ctx.enumerateIndirectObjects()) {
    const dict = obj instanceof PDFStream ? obj.dict : obj;
    if (!(dict instanceof PDFDict)) continue;
    const meta = dict.get(N('Metadata'));
    if (meta instanceof PDFRef ? ctx.lookup(meta) instanceof PDFStream : meta !== undefined) dict.delete(N('Metadata'));
    dict.delete(N('PieceInfo'));
    if (dict.get(N('Type')) !== N('Filespec')) dict.delete(N('LastModified'));
    // A JPEG is in the file as it came out of the camera or the scanner.
    const filter = dict.get(N('Filter'));
    const only = filter instanceof PDFArray && filter.size() === 1 ? filter.get(0) : filter;
    if (obj instanceof PDFRawStream && only === N('DCTDecode') && dict.get(N('Subtype')) === N('Image')) {
      const clean = stripJpeg(obj.contents);
      if (clean !== obj.contents) ctx.assign(ref, PDFRawStream.of(dict, clean));
    }
  }
  return save(doc);
}

// A JPEG without its EXIF, XMP, IPTC, thumbnails and comments, and without
// anything after its end. The picture data is copied, not re-encoded. What
// decides how it looks stays: the JFIF and Adobe colour markers, the ICC
// colour profile, and the EXIF Orientation field, alone in a fresh EXIF
// block (so no viewer shows the picture turned differently than before).
// A file that can't be followed is returned as it is.
// (leaflet_formats.py does the same for the pictures in documents.)
export function stripJpeg(data) {
  const n = data.length;
  if (n < 4 || data[0] !== 0xFF || data[1] !== 0xD8) return data;
  const starts = (at, text) => [...text].every((c, k) => data[at + k] === c.charCodeAt(0));
  const kept = [];
  let orientation = 1;
  let i = 2;
  for (;;) {
    if (i + 1 >= n || data[i] !== 0xFF) return data;
    const marker = data[i + 1];
    if (marker === 0xFF) { i += 1; continue; }
    if (marker === 0xD9) { kept.push(data.subarray(i, i + 2)); break; }
    if (marker === 0x01 || (marker >= 0xD0 && marker <= 0xD7)) { kept.push(data.subarray(i, i + 2)); i += 2; continue; }
    if (i + 3 >= n) return data;
    const size = (data[i + 2] << 8) | data[i + 3];
    let end = i + 2 + size;
    if (size < 2 || end > n) return data;
    if (marker === 0xDA) {            // a scan: its compressed data runs up to the next marker
      for (;;) {
        end = data.indexOf(0xFF, end);
        if (end < 0 || end + 1 >= n) { end = n; break; }      // cut short: all of it is picture data
        const next = data[end + 1];
        if (next === 0x00 || (next >= 0xD0 && next <= 0xD7)) end += 2;
        else if (next === 0xFF) end += 1;
        else break;
      }
      kept.push(data.subarray(i, end));
      if (end === n) break;
    } else if (marker === 0xE1 && starts(i + 4, 'Exif\0\0')) {
      orientation = exifOrientation(data.subarray(i + 10, end));
    } else if (marker === 0xFE || (marker >= 0xE0 && marker <= 0xEF)) {
      if ((marker === 0xE0 && starts(i + 4, 'JFIF\0')) || (marker === 0xE2 && starts(i + 4, 'ICC_PROFILE\0')) ||
          (marker === 0xEE && starts(i + 4, 'Adobe'))) kept.push(data.subarray(i, end));
    } else {
      kept.push(data.subarray(i, end));
    }
    i = end;
  }
  if (orientation !== 1) {
    const block = Uint8Array.from([0xFF, 0xE1, 0, 34, 0x45, 0x78, 0x69, 0x66, 0, 0,     // "Exif"
      0x4D, 0x4D, 0, 42, 0, 0, 0, 8, 0, 1, 0x01, 0x12, 0, 3, 0, 0, 0, 1, 0, orientation, 0, 0, 0, 0, 0, 0]);
    kept.splice(kept.length && kept[0][1] === 0xE0 ? 1 : 0, 0, block);
  }
  const out = new Uint8Array(2 + kept.reduce((sum, part) => sum + part.length, 0));
  out.set([0xFF, 0xD8]);
  let at = 2;
  for (const part of kept) { out.set(part, at); at += part.length; }
  return out;
}

// The Orientation (1-8) in an EXIF block's TIFF data; 1, upright, if there's none.
function exifOrientation(tiff) {
  const little = tiff[0] === 0x49 && tiff[1] === 0x49;
  if (!little && !(tiff[0] === 0x4D && tiff[1] === 0x4D)) return 1;
  const u16 = (at) => (at + 2 <= tiff.length ? (little ? tiff[at] | (tiff[at + 1] << 8) : (tiff[at] << 8) | tiff[at + 1]) : 0);
  const u32 = (at) => (little ? u16(at) + u16(at + 2) * 65536 : u16(at) * 65536 + u16(at + 2));
  if (u16(2) !== 42) return 1;
  const ifd = u32(4);
  const count = Math.min(u16(ifd), 512);
  for (let k = 0; k < count; k++) {
    const entry = ifd + 2 + 12 * k;
    if (entry + 12 > tiff.length) break;
    if (u16(entry) !== 0x0112) continue;
    const value = u16(entry + 8);
    return u16(entry + 2) === 3 && u32(entry + 4) === 1 && value >= 1 && value <= 8 ? value : 1;
  }
  return 1;
}

// Drop every object nothing points to any more (pages that were deleted,
// old versions of changed objects), so they don't stay hidden in the file.
// A page that isn't in the page tree counts as nothing even when bookmarks,
// links, structure tags or form fields still point at it: prune() takes
// those apart first, and whatever reference is left over is written as null.
function sweep(doc, removed = []) {
  const { PDFRef, PDFDict, PDFArray, PDFStream, PDFNull } = lib();
  const ctx = doc.context;
  const dead = prune(doc, removed);
  const gone = (obj) => obj instanceof PDFRef && dead.has(obj.toString());
  const seen = new Set();
  const stack = [];
  const visit = (obj) => {
    if (obj instanceof PDFRef) {
      const key = obj.toString();
      if (seen.has(key) || dead.has(key)) return;
      seen.add(key);
      const target = ctx.lookup(obj);
      if (target !== undefined) stack.push(target);
    } else if (obj !== undefined && obj !== null) {
      stack.push(obj);
    }
  };
  for (const [name, v] of Object.entries(ctx.trailerInfo)) {
    if (gone(v)) delete ctx.trailerInfo[name];
    else visit(v);
  }
  while (stack.length) {
    const obj = stack.pop();
    const dict = obj instanceof PDFStream ? obj.dict : obj;
    if (dict instanceof PDFDict) {
      for (const [k, v] of dict.entries()) {
        if (gone(v)) dict.delete(k);
        else visit(v);
      }
    } else if (obj instanceof PDFArray) {
      for (let i = 0; i < obj.size(); i++) {
        if (gone(obj.get(i))) obj.set(i, PDFNull);
        else visit(obj.get(i));
      }
    }
  }
  for (const [ref] of ctx.enumerateIndirectObjects()) {
    if (!seen.has(ref.toString())) ctx.delete(ref);
  }
}

// Find the pages that are no longer in the page tree and undo what still
// leads to them, so that neither they nor anything that describes them
// (a bookmark's title, a tag's text, a field's value) is kept:
//   - bookmarks that go to such a page are taken out of the outline (one
//     with bookmarks below it that still work stays, without a target),
//     and so is a heading that only held such bookmarks
//   - links to it are removed from the pages they're on
//   - named destinations and the open action that point at it are removed
//   - form fields lose the widgets that were on it, and go when none is left
//   - structure elements for its content are removed from the tag tree
// Returns the references (as strings) of everything that has to go.
function prune(doc, removed) {
  const { PDFRef, PDFDict, PDFArray, PDFName, PDFNumber, PDFString, PDFHexString } = lib();
  const ctx = doc.context;
  const N = (name) => PDFName.of(name);
  const deref = (obj) => (obj instanceof PDFRef ? ctx.lookup(obj) : obj);
  const dictOf = (obj) => { const v = deref(obj); return v instanceof PDFDict ? v : null; };
  const arrayOf = (obj) => { const v = deref(obj); return v instanceof PDFArray ? v : null; };
  const text = (obj) => (obj instanceof PDFString || obj instanceof PDFHexString || obj instanceof PDFName
    ? obj.decodeText() : null);
  // Filter an array in place.
  const keepOnly = (array, keep) => {
    for (let i = array.size() - 1; i >= 0; i--) if (!keep(array.get(i))) array.remove(i);
  };
  // Every node of a tree, parents before children; each node once, so a
  // file that loops back on itself can't keep this going.
  const walk = (roots, children) => {
    const nodes = [];
    const seen = new Set();
    const add = (entry) => {
      const dict = dictOf(entry);
      if (!dict || seen.has(dict)) return;
      seen.add(dict);
      nodes.push({ dict, ref: entry instanceof PDFRef ? entry : null });
    };
    roots.forEach(add);
    for (let i = 0; i < nodes.length; i++) children(nodes[i].dict).forEach(add);
    return nodes;
  };
  const kids = (dict) => { const k = arrayOf(dict.get(N('Kids'))); return k ? k.asArray() : []; };

  // The pages that are in the document.
  const pages = walk([doc.catalog.get(N('Pages'))], (d) => (d.get(N('Type')) === N('Page') ? [] : kids(d)));
  const inTree = new Set(pages.filter((p) => p.ref).map((p) => p.ref.toString()));
  const leaves = pages.filter((p) => !p.dict.get(N('Kids')));

  // The ones that aren't: those just removed, and any the file already had
  // lying about. (Template pages are kept out of the tree on purpose.)
  const names = dictOf(doc.catalog.get(N('Names')));
  const nameTree = (root) => walk([root], kids)
    .flatMap((n) => { const a = arrayOf(n.dict.get(N('Names'))); return a ? a.asArray() : []; });
  const templates = new Set(names ? nameTree(names.get(N('Templates'))).map(String) : []);
  const dead = new Set();
  const deadPages = [];
  const addPage = (ref, dict) => {
    const key = ref.toString();
    if (inTree.has(key) || dead.has(key)) return;
    dead.add(key);
    if (dict instanceof PDFDict) deadPages.push(dict);
  };
  for (const ref of removed) addPage(ref, ctx.lookup(ref));
  for (const [ref, obj] of ctx.enumerateIndirectObjects()) {
    if (obj instanceof PDFDict && obj.get(N('Type')) === N('Page') && !templates.has(ref.toString())) addPage(ref, obj);
  }
  if (!dead.size) return dead;
  const gone = (obj) => obj instanceof PDFRef && dead.has(obj.toString());

  // Their annotations go with them (form widgets among them).
  const annotsOf = (page) => { const a = arrayOf(page.get(N('Annots'))); return a ? a.asArray() : []; };
  const shown = new Set(leaves.flatMap((p) => annotsOf(p.dict)).map(String));
  const structKeys = new Set();     // their places in the structure tree's parent tree
  const structKey = (dict, name) => { const n = dict.get(N(name)); if (n instanceof PDFNumber) structKeys.add(n.asNumber()); };
  for (const page of deadPages) {
    structKey(page, 'StructParents');
    for (const annot of annotsOf(page)) {
      if (!(annot instanceof PDFRef) || shown.has(annot.toString())) continue;
      dead.add(annot.toString());
      const dict = dictOf(annot);
      if (!dict) continue;
      structKey(dict, 'StructParent');
      const popup = dict.get(N('Popup'));
      if (popup instanceof PDFRef && !shown.has(popup.toString())) dead.add(popup.toString());
    }
  }

  // Destinations: [page /Fit ...], a name for one, or << /D ... >>.
  const deadNames = new Set();
  const destGone = (dest, depth = 0) => {
    const d = deref(dest);
    if (d instanceof PDFArray) return d.size() > 0 && gone(d.get(0));
    if (d instanceof PDFDict) return depth < 4 && destGone(d.get(N('D')), depth + 1);
    const name = text(d);
    return name !== null && deadNames.has(name);
  };
  const actionGone = (action) => {
    const a = dictOf(action);
    return !!a && a.get(N('S')) === N('GoTo') && destGone(a.get(N('D')));
  };
  const goesNowhere = (dict) => destGone(dict.get(N('Dest'))) || actionGone(dict.get(N('A')));

  // Named destinations, old style (a dictionary) and new (a name tree,
  // which is written back as one flat list when something is taken out).
  const oldDests = dictOf(doc.catalog.get(N('Dests')));
  for (const [name, dest] of oldDests ? oldDests.entries() : []) {
    if (destGone(dest)) { deadNames.add(name.decodeText()); oldDests.delete(name); }
  }
  const destTree = names && dictOf(names.get(N('Dests')));
  if (destTree) {
    const flat = nameTree(destTree);
    const kept = [];
    for (let i = 0; i + 1 < flat.length; i += 2) {
      if (destGone(flat[i + 1])) deadNames.add(text(flat[i]) ?? '');
      else kept.push(flat[i], flat[i + 1]);
    }
    if (kept.length !== flat.length) {
      destTree.delete(N('Kids'));
      destTree.delete(N('Limits'));
      destTree.set(N('Names'), ctx.obj(kept));
    }
  }

  const open = doc.catalog.get(N('OpenAction'));
  if (open && (arrayOf(open) ? destGone(open) : actionGone(open))) doc.catalog.delete(N('OpenAction'));

  // Links on the pages that stay.
  for (const page of leaves) {
    const annots = arrayOf(page.dict.get(N('Annots')));
    if (!annots) continue;
    keepOnly(annots, (entry) => {
      const a = dictOf(entry);
      if (!a || !goesNowhere(a)) return true;
      if (a.get(N('Subtype')) === N('Link')) {
        if (entry instanceof PDFRef) dead.add(entry.toString());
        return false;
      }
      a.delete(N('Dest'));
      a.delete(N('A'));
      return true;
    });
  }

  // Bookmarks.
  const outlines = dictOf(doc.catalog.get(N('Outlines')));
  if (outlines) {
    const below = new Map();        // an item -> the items under it, in order
    const claimed = new Set([outlines]);
    const items = walk([doc.catalog.get(N('Outlines'))], (parent) => {
      const list = [];
      let next = parent.get(N('First'));
      while (next instanceof PDFRef && dictOf(next) && !claimed.has(dictOf(next))) {
        claimed.add(dictOf(next));
        list.push(next);
        next = dictOf(next).get(N('Next'));
      }
      below.set(parent, list);
      return list;
    });
    const stays = new Map();
    const visible = new Map();      // how many lines an item shows under it when it's open
    for (let i = items.length - 1; i >= 0; i--) {
      const { dict, ref } = items[i];
      const top = dict === outlines;
      const had = (below.get(dict) || []).length;
      const list = (below.get(dict) || []).filter((k) => stays.get(dictOf(k)));
      const nowhere = !top && goesNowhere(dict);
      if (nowhere) { dict.delete(N('Dest')); dict.delete(N('A')); }
      // A heading with no target of its own goes when all it held is gone.
      const emptied = had > 0 && !dict.get(N('Dest')) && !dict.get(N('A'));
      const keep = top || list.length > 0 || !(nowhere || emptied);
      stays.set(dict, keep);
      if (!keep) { if (ref) dead.add(ref.toString()); continue; }
      const count = dict.get(N('Count'));
      const closed = !top && !(count instanceof PDFNumber && count.asNumber() > 0);
      const lines = list.reduce((n, k) => n + 1 + (visible.get(dictOf(k)) || 0), 0);
      visible.set(dict, closed ? 0 : lines);
      for (const name of ['First', 'Last', 'Count']) dict.delete(N(name));
      if (!list.length) continue;
      dict.set(N('First'), list[0]);
      dict.set(N('Last'), list[list.length - 1]);
      dict.set(N('Count'), PDFNumber.of(closed ? -lines : lines));
      list.forEach((k, n) => {
        const item = dictOf(k);
        if (ref) item.set(N('Parent'), ref);
        if (n > 0) item.set(N('Prev'), list[n - 1]); else item.delete(N('Prev'));
        if (n + 1 < list.length) item.set(N('Next'), list[n + 1]); else item.delete(N('Next'));
      });
    }
    if (!outlines.get(N('First'))) doc.catalog.delete(N('Outlines'));
  }

  // Form fields.
  const form = dictOf(doc.catalog.get(N('AcroForm')));
  const fields = form && arrayOf(form.get(N('Fields')));
  if (fields) {
    const all = walk(fields.asArray(), kids);
    const stays = new Map();
    const staying = (entry) => { const d = dictOf(entry); return !d || stays.get(d) !== false; };
    for (let i = all.length - 1; i >= 0; i--) {
      const { dict, ref } = all[i];
      const under = arrayOf(dict.get(N('Kids')));
      let keep = !(ref && dead.has(ref.toString()));
      if (keep && under && under.size() > 0) {
        keepOnly(under, staying);
        keep = under.size() > 0;
      }
      stays.set(dict, keep);
      if (!keep && ref) dead.add(ref.toString());
    }
    keepOnly(fields, staying);
    const order = arrayOf(form.get(N('CO')));
    if (order) keepOnly(order, staying);
  }

  // Structure tags. What an element holds (/K) is a mix of other elements
  // and pieces of page content: a number, or a dictionary of /Type /MCR
  // (marked content) or /OBJR (an annotation).
  const structRoot = dictOf(doc.catalog.get(N('StructTreeRoot')));
  if (structRoot) {
    const content = (entry) => {
      if (entry instanceof PDFNumber) return true;
      const d = dictOf(entry);
      return !d || d.get(N('Type')) === N('MCR') || d.get(N('Type')) === N('OBJR');
    };
    const held = (dict) => {
      const k = dict.get(N('K'));
      const list = arrayOf(k);
      return list ? list.asArray() : (k === undefined ? [] : [k]);
    };
    const elements = walk([doc.catalog.get(N('StructTreeRoot'))], (d) => held(d).filter((e) => !content(e)));
    const stays = new Map();
    for (let i = elements.length - 1; i >= 0; i--) {
      const { dict, ref } = elements[i];
      const top = dict === structRoot;
      const page = dict.get(N('Pg'));
      const staying = (entry) => {
        if (entry instanceof PDFNumber) return !gone(page);
        const d = dictOf(entry);
        if (!d) return true;
        if (!content(entry)) return stays.get(d) !== false;
        return !gone(d.get(N('Pg')) || page) && !gone(d.get(N('Obj')));
      };
      const before = held(dict).length;
      const list = arrayOf(dict.get(N('K')));
      if (list) keepOnly(list, staying);
      else if (before && !staying(dict.get(N('K')))) dict.delete(N('K'));
      const keep = top || held(dict).length > 0 || (before === 0 && !gone(page));
      if (keep && gone(page)) dict.delete(N('Pg'));
      stays.set(dict, keep);
      if (!keep && ref) dead.add(ref.toString());
    }
    // The parent tree lists, for every page and annotation, the elements
    // its content belongs to; the removed pages' entries go.
    for (const node of walk([structRoot.get(N('ParentTree'))], kids)) {
      const nums = arrayOf(node.dict.get(N('Nums')));
      for (let i = nums ? nums.size() - 2 : -1; i >= 0; i -= 2) {
        const n = nums.get(i);
        if (n instanceof PDFNumber && structKeys.has(n.asNumber())) { nums.remove(i + 1); nums.remove(i); }
      }
    }
  }
  return dead;
}
