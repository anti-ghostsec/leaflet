// PDF page operations.   node tests/test_pdfops.mjs
// Uses the pdf-lib copy that ships in ui/vendor and the test files from fixtures.py.
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { inflateSync } from 'node:zlib';
import assert from 'node:assert/strict';

const here = dirname(fileURLToPath(import.meta.url));
globalThis.PDFLib = createRequire(import.meta.url)(join(here, '../ui/vendor/pdf-lib.min.js'));
const ops = await import('../ui/pdfops.mjs');
const { PDFDocument, PDFName, PDFDict, PDFArray, PDFRef, PDFStream, PDFNumber } = globalThis.PDFLib;

// PDF.js, the copy the window uses, to see that what's saved still opens in
// it. (Older versions of node lack a few small things it expects; if it
// still can't run here, the checks that need it are left out, and say so.)
Promise.try ??= (fn, ...args) => new Promise((resolve) => resolve(fn(...args)));
Promise.withResolvers ??= function withResolvers() {
  const out = {};
  out.promise = new Promise((resolve, reject) => Object.assign(out, { resolve, reject }));
  return out;
};
for (const M of [Map, WeakMap]) {
  M.prototype.getOrInsertComputed ??= function getOrInsertComputed(key, make) {
    if (!this.has(key)) this.set(key, make(key));
    return this.get(key);
  };
  M.prototype.getOrInsert ??= function getOrInsert(key, value) {
    if (!this.has(key)) this.set(key, value);
    return this.get(key);
  };
}
Uint8Array.prototype.toHex ??= function toHex() { return Buffer.from(this.buffer, this.byteOffset, this.length).toString('hex'); };
async function loadPdfjs() {
  const warn = console.warn;
  console.warn = () => {};
  const lib = await import('../ui/vendor/pdfjs/pdf.min.mjs');
  console.warn = warn;
  lib.GlobalWorkerOptions.workerSrc = join(here, '../ui/vendor/pdfjs/pdf.worker.min.mjs');
  return lib;
}

const dir = mkdtempSync(join(tmpdir(), 'leaflet-test-'));
process.on('exit', () => rmSync(dir, { recursive: true, force: true }));
execFileSync('python3', [join(here, 'fixtures.py'), dir]);
const files = {};
const KINDS = ['outline', 'link', 'struct', 'form', 'names', 'open'];
for (const name of ['pages.pdf', 'extra.pdf', 'form.pdf', 'refs-all.pdf', ...KINDS.map((k) => `refs-${k}.pdf`),
  'photo.pdf', 'encrypted.pdf', 'photo-1.jpg', 'plain-1.jpg', 'photo-6.jpg', 'plain-6.jpg']) {
  files[name] = readFileSync(join(dir, name));
}
const file = (name) => new Uint8Array(files[name]);

// Whether PDF.js runs here is tried out in a second node, because when it
// doesn't, it takes the whole process down with it.
if (process.env.LEAFLET_PDFJS_PROBE) {
  const task = (await loadPdfjs()).getDocument({ data: file('pages.pdf'), verbosity: 0 });
  await (await (await task.promise).getPage(1)).getTextContent();
  process.exit(0);
}
const probe = spawnSync(process.execPath, [fileURLToPath(import.meta.url)],
  { env: { ...process.env, LEAFLET_PDFJS_PROBE: '1' }, stdio: 'ignore', timeout: 60000 });
const pdfjs = probe.status === 0 ? await loadPdfjs() : null;
if (!pdfjs) console.log(`note: PDF.js doesn't run in node ${process.version}; saved files are not opened with it here`);

const latin = new TextDecoder('latin1');
async function labels(bytes) {
  const doc = await PDFDocument.load(bytes, { updateMetadata: false });
  return doc.getPages().map((p) => {
    const c = doc.context.lookup(p.node.get(PDFName.of('Contents')));
    return /\((.*?)\) Tj/.exec(latin.decode(c.contents))[1];
  });
}
async function rotations(bytes) {
  const doc = await PDFDocument.load(bytes, { updateMetadata: false });
  return doc.getPages().map((p) => p.getRotation().angle);
}

let failed = 0;
async function test(name, fn) {
  try {
    await fn();
    console.log(`ok   ${name}`);
  } catch (e) {
    failed++;
    console.log(`FAIL ${name}\n     ${e.stack.split('\n').slice(0, 3).join('\n     ')}`);
  }
}

const pages = file('pages.pdf');

await test('rotate turns only the chosen pages, both ways', async () => {
  let b = await ops.rotate(pages, [1, 3], 90);
  assert.deepEqual(await rotations(b), [0, 90, 0, 90, 0]);
  b = await ops.rotate(b, [1], -90);
  b = await ops.rotate(b, [3], 270);
  assert.deepEqual(await rotations(b), [0, 0, 0, 0, 0]);
});

await test('delete removes pages and their contents from the file', async () => {
  const b = await ops.remove(pages, [2, 0]);
  assert.deepEqual(await labels(b), ['Page 2', 'Page 4', 'Page 5']);
  const text = latin.decode(b);
  assert.ok(text.includes('(Page 2) Tj'));
  assert.ok(!text.includes('(Page 3) Tj'), 'deleted page still in the file');
  assert.ok(!text.includes('(Page 1) Tj'), 'deleted page still in the file');
});

await test('delete refuses to remove every page', async () => {
  await assert.rejects(ops.remove(pages, [0, 1, 2, 3, 4]));
});

await test('reorder and move', async () => {
  const b = await ops.reorder(pages, [4, 3, 2, 1, 0]);
  assert.deepEqual(await labels(b), ['Page 5', 'Page 4', 'Page 3', 'Page 2', 'Page 1']);
  assert.deepEqual(ops.moveOrder(5, [0], 3), [1, 2, 0, 3, 4]);
  assert.deepEqual(ops.moveOrder(5, [3, 4], 0), [3, 4, 0, 1, 2]);
  assert.deepEqual(ops.moveOrder(5, [1], 5), [0, 2, 3, 4, 1]);
  assert.deepEqual(await labels(await ops.reorder(pages, ops.moveOrder(5, [0, 1], 4))),
    ['Page 3', 'Page 4', 'Page 1', 'Page 2', 'Page 5']);
  await assert.rejects(ops.reorder(pages, [0, 0, 1, 2, 3]));
});

await test('merge inserts another PDF where asked', async () => {
  const b = await ops.merge(pages, file('extra.pdf'), 1);
  assert.deepEqual(await labels(b), ['Page 1', 'Extra 1', 'Extra 2', 'Page 2', 'Page 3', 'Page 4', 'Page 5']);
  const end = await ops.merge(pages, file('extra.pdf'));
  assert.equal((await labels(end)).at(-1), 'Extra 2');
});

await test('merging a form keeps its fields working', async () => {
  const b = await ops.merge(pages, file('form.pdf'));
  const doc = await PDFDocument.load(b);
  const names = doc.getForm().getFields().map((f) => f.getName()).sort();
  assert.deepEqual(names, ['agree', 'name']);
});

await test('finalize strips author, programs, dates and XMP, keeps the title', async () => {
  const b = await ops.finalize(pages);
  const doc = await PDFDocument.load(b, { updateMetadata: false });
  const info = doc.context.lookup(doc.context.trailerInfo.Info);
  assert.ok(info instanceof PDFDict);
  assert.deepEqual(info.keys().map(String), ['/Title']);
  assert.equal(doc.getTitle(), 'Sample pages');
  assert.equal(doc.catalog.get(PDFName.of('Metadata')), undefined);
  assert.ok(!latin.decode(b).includes('Sample Author'));
  assert.ok(!latin.decode(b).includes('pdf-lib'));
  assert.deepEqual(await labels(b), ['Page 1', 'Page 2', 'Page 3', 'Page 4', 'Page 5']);
});

await test('finalize removes the dates on annotations', async () => {
  const b = await ops.finalize(file('form.pdf'));
  const doc = await PDFDocument.load(b, { updateMetadata: false });
  const annots = doc.getPage(0).node.lookup(PDFName.of('Annots'), PDFArray);
  const dicts = annots.asArray().map((r) => doc.context.lookup(r));
  assert.equal(dicts.length, 2);
  assert.ok(dicts.every((d) => d.get(PDFName.of('M')) === undefined));
  assert.deepEqual(doc.getForm().getFields().map((f) => f.getName()).sort(), ['agree', 'name']);
});

await test('an encrypted PDF is told apart, so the window can say what it can\'t do with it', async () => {
  const enc = file('encrypted.pdf');
  await assert.rejects(ops.finalize(enc), ops.EncryptedError);
  await assert.rejects(ops.remove(enc, [0]), ops.EncryptedError);
});

await test('page operations themselves add no metadata', async () => {
  for (const b of [await ops.rotate(file('form.pdf'), [0], 90), await ops.remove(pages, [0])]) {
    const doc = await PDFDocument.load(b, { updateMetadata: false });
    assert.ok(!latin.decode(b).includes('pdf-lib'));
    assert.ok(!/ModDate/.test(latin.decode(b)));
  }
});

await test('form fields survive rotate and reorder', async () => {
  let b = await ops.rotate(file('form.pdf'), [0], 90);
  b = await ops.reorder(b, [0]);
  const names = (await PDFDocument.load(b)).getForm().getFields().map((f) => f.getName()).sort();
  assert.deepEqual(names, ['agree', 'name']);
});

// ------------------------------------------------------------------ authors and picture metadata

await test('a JPEG loses its EXIF, XMP, IPTC and comments, and nothing else', async () => {
  // plain-N.jpg is the same picture made without the metadata: only the
  // colour profile, the JFIF and Adobe markers and the orientation are left.
  for (const n of [1, 6]) {
    const out = ops.stripJpeg(file(`photo-${n}.jpg`));
    assert.deepEqual(Buffer.from(out), Buffer.from(file(`plain-${n}.jpg`)));
    assert.deepEqual(Buffer.from(ops.stripJpeg(out)), Buffer.from(out));
  }
  assert.ok(latin.decode(file('plain-6.jpg')).includes('ICC_PROFILE') && !latin.decode(file('plain-1.jpg')).includes('Exif'));
  const photo = file('photo-6.jpg');
  const cut = photo.subarray(0, photo.lastIndexOf(0xD9) - 2);      // ends inside the picture data
  assert.deepEqual(Buffer.from(ops.stripJpeg(cut)), Buffer.from(file('plain-6.jpg').subarray(0, file('plain-6.jpg').length - 3)));
  for (const junk of [new Uint8Array(0), Uint8Array.of(0xFF, 0xD8), Uint8Array.of(0xFF, 0xD8, 0xFF, 0xE1, 0xFF, 0xFF),
    photo.subarray(0, 40), Uint8Array.of(1, 2, 3, 4, 5)]) {
    assert.equal(ops.stripJpeg(junk), junk);                  // left as it was
  }
});

await test('finalize removes who made the notes, and the metadata inside pictures', async () => {
  const before = file('photo.pdf');
  const marks = ['Sample Camera', 'Sample Photographer', 'Sample caption', 'Sample comment', 'Sample thumbnail',
    'Sample second picture', 'Sample trailing data', 'Sample private', 'GPS', 'Sample Reviewer', 'sample-note-id',
    'xmpmeta', '/NM', '/CreationDate', '/LastModified', '/PieceInfo', '/Metadata', 'D:2020'];
  for (const mark of marks) assert.ok(everything(before).includes(mark), `the test file should have ${mark}`);
  const b = await ops.finalize(before);
  const text = everything(b);
  for (const mark of marks) assert.ok(!text.includes(mark), `${mark} is still in the file`);
  for (const stays of ['A note that stays', 'A reply that stays', 'A value', '/IRT', '/Popup']) assert.ok(text.includes(stays), stays);
  assert.deepEqual(await dangling(b), []);

  const doc = await PDFDocument.load(b, { updateMetadata: false });
  assert.deepEqual(doc.getForm().getFields().map((f) => f.getName()), ['name']);      // a field's /T is its name
  const xobjects = doc.getPage(0).node.Resources().lookup(PDFName.of('XObject'), PDFDict);
  const picture = (name) => Buffer.from(doc.context.lookup(xobjects.get(PDFName.of(name))).contents);
  assert.deepEqual(picture('Im1'), Buffer.from(file('plain-6.jpg')));      // the picture data itself is untouched
  assert.deepEqual(picture('Im2'), Buffer.from(file('plain-1.jpg')));
  assert.ok(text.includes('ICC_PROFILE'));

  if (pdfjs) {
    const task = pdfjs.getDocument({ data: b.slice(), verbosity: 0, stopAtErrors: true });
    const page = await (await task.promise).getPage(1);
    const ops2 = await page.getOperatorList();
    assert.equal(ops2.fnArray.filter((fn) => fn === pdfjs.OPS.paintImageXObject).length, 2);
    const annots = await page.getAnnotations();
    assert.deepEqual(annots.map((a) => a.subtype).sort(), ['FreeText', 'Popup', 'Text', 'Widget']);
    assert.ok(annots.every((a) => !a.titleObj || !a.titleObj.str));
    await task.destroy();
  }
});

// ------------------------------------------------------------------ deleted pages are really gone

// The whole file as text: as it is, and every stream in it unpacked.
function everything(bytes) {
  const raw = latin.decode(bytes);
  const parts = [raw];
  const re = /stream\r?\n/g;
  for (let m = re.exec(raw); m; m = re.exec(raw)) {
    const end = raw.indexOf('endstream', re.lastIndex);
    if (end < 0) break;
    try { parts.push(latin.decode(inflateSync(bytes.subarray(re.lastIndex, end)))); } catch { /* not deflated */ }
  }
  return parts.join('\n');
}
const MARK = 'ZQXJ';        // on everything that belongs to page 2 of the refs-*.pdf files (fixtures.py)
const leftOf = (bytes) => [...new Set(everything(bytes).match(/[\w ]*ZQXJ[\w!]*/g) || [])];

// Every reference in the file leads to an object that is there.
async function dangling(bytes) {
  const doc = await PDFDocument.load(bytes, { updateMetadata: false });
  const ctx = doc.context;
  const out = [];
  const look = (obj, where) => {
    if (obj instanceof PDFRef) { if (ctx.lookup(obj) === undefined) out.push(`${where} -> ${obj}`); return; }
    const dict = obj instanceof PDFStream ? obj.dict : obj;
    if (dict instanceof PDFDict) for (const [k, v] of dict.entries()) look(v, `${where}${k}`);
    else if (obj instanceof PDFArray) obj.asArray().forEach((v) => look(v, where));
  };
  for (const [ref, obj] of ctx.enumerateIndirectObjects()) look(obj, `${ref} `);
  for (const [k, v] of Object.entries(ctx.trailerInfo)) look(v, `trailer /${k}`);
  return out;
}

// The bookmarks as [title, [children]], read the way a viewer follows them,
// checking that the links between them agree with each other.
async function bookmarks(bytes) {
  const doc = await PDFDocument.load(bytes, { updateMetadata: false });
  const N = (n) => PDFName.of(n);
  const level = (parent, parentRef) => {
    const out = [];
    let ref = parent.get(N('First'));
    let prev;
    while (ref) {
      const item = doc.context.lookup(ref, PDFDict);
      assert.equal(String(item.get(N('Parent'))), String(parentRef), 'bookmark /Parent');
      assert.equal(String(item.get(N('Prev'))), String(prev), 'bookmark /Prev');
      const below = level(item, ref);
      const count = item.get(N('Count'));
      assert.equal(count instanceof PDFNumber ? Math.abs(count.asNumber()) > 0 : false, below.length > 0, 'bookmark /Count');
      out.push(below.length ? [item.get(N('Title')).decodeText(), below] : item.get(N('Title')).decodeText());
      prev = ref;
      ref = item.get(N('Next'));
      assert.ok(out.length < 100);
    }
    assert.equal(String(parent.get(N('Last'))), String(prev), 'bookmark /Last');
    return out;
  };
  const ref = doc.catalog.get(N('Outlines'));
  return ref ? level(doc.context.lookup(ref, PDFDict), ref) : null;
}

// Open it the way the window does and go through everything PDF.js reads.
async function opensInPdfjs(bytes) {
  if (!pdfjs) return null;
  const task = pdfjs.getDocument({ data: bytes.slice(), isEvalSupported: false, enableXfa: false, useSystemFonts: true,
    stopAtErrors: true, verbosity: 0 });
  const doc = await task.promise;
  const keys = (m) => (m instanceof Map ? [...m.keys()] : Object.keys(m || {})).sort();
  const seen = { pages: [], links: [], outline: [], fields: keys(await doc.getFieldObjects()) };
  const pageOf = async (dest) => {
    const d = typeof dest === 'string' ? await doc.getDestination(dest) : dest;
    assert.ok(Array.isArray(d), `destination ${JSON.stringify(dest)} leads nowhere`);
    return await doc.getPageIndex(d[0]);
  };
  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i);
    seen.pages.push((await page.getTextContent()).items.map((t) => t.str).join(''));
    await page.getOperatorList();
    await page.getStructTree();
    for (const a of await page.getAnnotations()) if (a.subtype === 'Link') seen.links.push([i - 1, await pageOf(a.dest)]);
  }
  const titles = async (items) => {
    const out = [];
    for (const item of items || []) {
      if (item.dest) await pageOf(item.dest);
      const below = await titles(item.items);
      out.push(below.length ? [item.title, below] : item.title);
    }
    return out;
  };
  seen.outline = await titles(await doc.getOutline());
  seen.dests = keys(await doc.getDestinations());
  seen.open = await doc.getOpenAction();
  await task.destroy();
  return seen;
}

// qpdf, when it's installed, as a second opinion on the file's structure.
const HAVE_QPDF = spawnSync('qpdf', ['--version']).status === 0;
function qpdfCheck(bytes, name) {
  if (!HAVE_QPDF) return;
  const path = join(dir, name);
  writeFileSync(path, bytes);
  const r = spawnSync('qpdf', ['--check', path], { encoding: 'utf8' });
  assert.ok(r.status === 0, `qpdf --check: ${r.stdout}${r.stderr}`);
}

async function goneAndValid(bytes, label) {
  assert.deepEqual(leftOf(bytes), [], `${label}: something of the deleted page is still in the file`);
  assert.ok(!everything(bytes).includes('IMGZQXJ!'), `${label}: the deleted page's picture is still in the file`);
  assert.deepEqual(await dangling(bytes), [], `${label}: references to objects that aren't there`);
  qpdfCheck(bytes, `${label.replace(/\W+/g, '-')}.pdf`);
  const seen = await opensInPdfjs(bytes);
  if (seen) assert.deepEqual(seen.pages, ['Kept 1', 'Kept 3'], label);
  assert.deepEqual(await labels(bytes), ['Kept 1', 'Kept 3'], label);
  await bookmarks(bytes);
  return seen;
}

// What should be left of each kind of reference once page 2 is deleted.
const KEPT_OUTLINE = ['Kept one', ['Kept group', ['Kept leaf']], 'Kept three'];
const expect = {
  outline: (seen) => seen && assert.deepEqual(seen.outline, KEPT_OUTLINE),
  link: (seen) => seen && assert.deepEqual(seen.links, [[0, 1], [1, 0]]),
  struct: () => {},
  form: (seen) => seen && assert.deepEqual(seen.fields, ['both', 'kept']),
  names: (seen) => seen && assert.deepEqual(seen.dests, ['old3', 'to3']),
  open: (seen) => seen && assert.equal(seen.open, null),
};

// A file as it is when a page was taken out of the page tree and nothing else done.
async function unlinked(bytes, index) {
  const doc = await PDFDocument.load(bytes, { updateMetadata: false });
  doc.removePage(index);
  return await doc.save({ useObjectStreams: false, addDefaultPage: false, updateFieldAppearances: false });
}

for (const kind of [...KINDS, 'all']) {
  const what = kind === 'all' ? 'all of these at once' : { outline: 'bookmarks', link: 'links', struct: 'structure tags',
    form: 'form fields', names: 'named destinations', open: 'the open action' }[kind];
  await test(`a deleted page is gone from the file though ${what} pointed at it`, async () => {
    const before = file(`refs-${kind}.pdf`);
    assert.ok(leftOf(before).length > 0 && everything(before).includes('IMGZQXJ!'));
    const checks = kind === 'all' ? KINDS : [kind];
    const deleted = await ops.remove(before, [1]);
    for (const k of checks) expect[k](await goneAndValid(deleted, `${kind} deleted`));
    if (checks.includes('outline')) assert.deepEqual(await bookmarks(deleted), KEPT_OUTLINE);
    if (checks.includes('form')) {
      const fields = (await PDFDocument.load(deleted)).getForm().getFields().map((f) => f.getName()).sort();
      assert.deepEqual(fields, ['both', 'kept']);
    }
    for (const k of checks) expect[k](await goneAndValid(await ops.finalize(deleted), `${kind} deleted and saved`));
    // a page some other program (or an older Leaflet) left out of the page tree
    const stray = await unlinked(before, 1);
    assert.ok(leftOf(stray).length > 0);
    for (const k of checks) expect[k](await goneAndValid(await ops.finalize(stray), `${kind} stray page, saved`));
  });
}

await test('pages that stay keep their bookmarks, links, fields and tags', async () => {
  const all = file('refs-all.pdf');
  const full = ['Kept one', ['Removed chapter ZQXJ', ['Removed section ZQXJ']], 'Removed by name ZQXJ',
    ['Kept group', ['Removed leaf ZQXJ', 'Kept leaf']], 'Kept three'];
  for (const b of [await ops.finalize(all), await ops.reorder(all, [2, 1, 0]), await ops.rotate(all, [1], 90)]) {
    assert.deepEqual(await bookmarks(b), full);
    assert.deepEqual(await dangling(b), []);
    const seen = await opensInPdfjs(b);
    if (seen) {
      assert.equal(seen.links.length, 4);
      assert.deepEqual(seen.fields, ['alone', 'both', 'gone', 'kept']);
      assert.deepEqual(seen.dests, ['old2', 'old3', 'to2', 'to3']);
    }
    assert.ok(everything(b).includes('Removed text ZQXJ') && everything(b).includes('IMGZQXJ!'));
  }
  // deleting another page leaves page 2 and everything about it alone
  const b = await ops.remove(all, [0]);
  if (pdfjs) assert.deepEqual((await opensInPdfjs(b)).pages, ['Removed page ZQXJ', 'Kept 3']);
  assert.deepEqual(await bookmarks(b), full.slice(1));
  assert.deepEqual(await dangling(b), []);
});

await test('deleting the pages every bookmark points at leaves no outline', async () => {
  let b = await ops.remove(file('refs-outline.pdf'), [2, 0]);
  assert.deepEqual(await bookmarks(b), [['Removed chapter ZQXJ', ['Removed section ZQXJ']], 'Removed by name ZQXJ',
    ['Kept group', ['Removed leaf ZQXJ']]]);
  if (pdfjs) assert.deepEqual((await opensInPdfjs(b)).pages, ['Removed page ZQXJ']);
  b = await ops.remove(await ops.merge(b, file('extra.pdf')), [0]);
  assert.equal(await bookmarks(b), null);
  assert.deepEqual(leftOf(b), []);
  assert.deepEqual(await dangling(b), []);
  if (pdfjs) assert.deepEqual((await opensInPdfjs(b)).pages, ['Extra 1', 'Extra 2']);
});

await test('a bookmark list that loops back on itself doesn\'t hang the save', async () => {
  const doc = await PDFDocument.load(file('refs-outline.pdf'), { updateMetadata: false });
  const outlines = doc.context.lookup(doc.catalog.get(PDFName.of('Outlines')), PDFDict);
  const first = outlines.get(PDFName.of('First'));
  doc.context.lookup(first, PDFDict).set(PDFName.of('Next'), first);
  doc.context.lookup(first, PDFDict).set(PDFName.of('First'), first);
  const looped = await doc.save({ useObjectStreams: false });
  const b = await ops.remove(looped, [1]);
  assert.deepEqual(leftOf(b), []);
  assert.deepEqual(await bookmarks(b), ['Kept one']);
});

await test('template pages, kept outside the page tree on purpose, stay', async () => {
  const doc = await PDFDocument.load(pages, { updateMetadata: false });
  const template = doc.getPage(4).ref;
  doc.removePage(4);
  doc.catalog.set(PDFName.of('Names'), doc.context.obj({ Templates: { Names: [globalThis.PDFLib.PDFString.of('t'), template] } }));
  const b = await ops.finalize(await doc.save({ useObjectStreams: false }));
  assert.deepEqual(await labels(b), ['Page 1', 'Page 2', 'Page 3', 'Page 4']);
  assert.ok(everything(b).includes('(Page 5) Tj'));
  assert.deepEqual(await dangling(b), []);
});

if (failed) {
  console.log(`${failed} failed`);
  process.exit(1);
}
console.log('all PDF operation tests passed');
