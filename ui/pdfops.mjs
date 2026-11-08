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

async function save(doc) {
  sweep(doc);
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
  for (const i of [...new Set(indices)].sort((a, b) => b - a)) doc.removePage(i);
  return save(doc);
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
export async function finalize(bytes) {
  const { PDFName, PDFDict, PDFArray, PDFString, PDFHexString } = lib();
  const doc = await load(bytes);
  const info = doc.context.lookup(doc.context.trailerInfo.Info);
  let title = null;
  if (info instanceof PDFDict) {
    const t = info.get(PDFName.of('Title'));
    if (t instanceof PDFString || t instanceof PDFHexString) title = t;
  }
  if (title) {
    doc.context.trailerInfo.Info = doc.context.register(doc.context.obj({ Title: title }));
  } else {
    delete doc.context.trailerInfo.Info;
  }
  delete doc.context.trailerInfo.ID;
  doc.catalog.delete(PDFName.of('Metadata'));
  doc.catalog.delete(PDFName.of('PieceInfo'));
  for (const page of doc.getPages()) {
    page.node.delete(PDFName.of('PieceInfo'));
    page.node.delete(PDFName.of('Metadata'));
    // Annotations carry the time they were made or changed.
    const annots = page.node.lookupMaybe(PDFName.of('Annots'), PDFArray);
    for (let i = 0; annots && i < annots.size(); i++) {
      const a = doc.context.lookup(annots.get(i));
      if (!(a instanceof PDFDict)) continue;
      a.delete(PDFName.of('M'));
      a.delete(PDFName.of('CreationDate'));
      const popup = a.lookupMaybe(PDFName.of('Popup'), PDFDict);
      if (popup) { popup.delete(PDFName.of('M')); popup.delete(PDFName.of('CreationDate')); }
    }
  }
  return save(doc);
}

// Drop every object nothing points to any more (pages that were deleted,
// old versions of changed objects), so they don't stay hidden in the file.
function sweep(doc) {
  const { PDFRef, PDFDict, PDFArray, PDFStream } = lib();
  const ctx = doc.context;
  const seen = new Set();
  const stack = [];
  const visit = (obj) => {
    if (obj instanceof PDFRef) {
      const key = obj.toString();
      if (seen.has(key)) return;
      seen.add(key);
      const target = ctx.lookup(obj);
      if (target !== undefined) stack.push(target);
    } else if (obj !== undefined && obj !== null) {
      stack.push(obj);
    }
  };
  for (const v of Object.values(ctx.trailerInfo)) visit(v);
  while (stack.length) {
    const obj = stack.pop();
    if (obj instanceof PDFStream) {
      for (const [, v] of obj.dict.entries()) visit(v);
    } else if (obj instanceof PDFDict) {
      for (const [, v] of obj.entries()) visit(v);
    } else if (obj instanceof PDFArray) {
      for (const v of obj.asArray()) visit(v);
    }
  }
  for (const [ref] of ctx.enumerateIndirectObjects()) {
    if (!seen.has(ref.toString())) ctx.delete(ref);
  }
}
