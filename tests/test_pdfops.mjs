// PDF page operations.   node tests/test_pdfops.mjs
// Uses the pdf-lib copy that ships in ui/vendor and the test files from fixtures.py.
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const here = dirname(fileURLToPath(import.meta.url));
globalThis.PDFLib = createRequire(import.meta.url)(join(here, '../ui/vendor/pdf-lib.min.js'));
const ops = await import('../ui/pdfops.mjs');
const { PDFDocument, PDFName, PDFDict, PDFArray } = globalThis.PDFLib;

const dir = mkdtempSync(join(tmpdir(), 'leaflet-test-'));
execFileSync('python3', [join(here, 'fixtures.py'), dir]);
const files = {};
for (const name of ['pages.pdf', 'extra.pdf', 'form.pdf']) files[name] = readFileSync(join(dir, name));
rmSync(dir, { recursive: true });
const file = (name) => new Uint8Array(files[name]);

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

if (failed) {
  console.log(`${failed} failed`);
  process.exit(1);
}
console.log('all PDF operation tests passed');
