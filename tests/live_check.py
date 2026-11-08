#!/usr/bin/env python3
"""Drive the real Leaflet window on a (virtual) screen and check what it does.

    DISPLAY=:99 python3 tests/live_check.py OUTDIR

Uses only the test files from fixtures.py. Every request the web view could
make goes to a local listener (as the "proxy", and as the address the
hostile PDF points at), which counts them: the check fails if there's any.
Screenshots of the window are written to OUTDIR/shots.
"""
import http.server
import json
import os
import resource
import subprocess
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else 'live-out')
SHOTS = os.path.join(OUT, 'shots')
FILES = os.path.join(OUT, 'files')
os.makedirs(SHOTS, exist_ok=True)

HITS = []


class Counter(http.server.BaseHTTPRequestHandler):
    def _hit(self):
        HITS.append(f'{self.command} {self.path}')
        self.send_response(204)
        self.end_headers()
    do_GET = do_POST = do_CONNECT = do_HEAD = do_PUT = _hit

    def log_message(self, *_):
        pass


server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Counter)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

os.environ['LEAFLET_TEST'] = '1'
os.environ['LEAFLET_TEST_PROXY'] = f'http://127.0.0.1:{PORT}'
os.environ.pop('LEAFLET_THEME', None)

import fixtures  # noqa: E402
fixtures.write_all(FILES, PORT)

import leaflet  # noqa: E402
import leaflet_formats as lf  # noqa: E402
from gi.repository import GLib, WebKit  # noqa: E402

RESULTS = []
LINKS = []
WRITES = []


def check(name, ok, detail=''):
    RESULTS.append((name, bool(ok), detail))
    print(f'{"ok  " if ok else "FAIL"} {name}{"  — " + str(detail) if detail and not ok else ""}', flush=True)


class Driver:
    """Runs a generator of steps on the GTK main loop."""

    def __init__(self, app, gen):
        self.app, self.gen = app, gen
        GLib.timeout_add(300, self.step, None)

    def step(self, value):
        try:
            what = self.gen.send(value)
        except StopIteration:
            self.app.quit()
            return False
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            check('scenario ran to the end', False, 'exception')
            self.app.quit()
            return False
        kind = what[0]
        if kind == 'sleep':
            GLib.timeout_add(int(what[1] * 1000), self.step, None)
        elif kind == 'js':
            self.js(what[1], what[2] if len(what) > 2 else None)
        elif kind == 'until':
            self.until(what[1], what[2], time.time() + what[3] if len(what) > 3 else time.time() + 20)
        elif kind == 'shot':
            what[1].web.get_snapshot(WebKit.SnapshotRegion.VISIBLE, WebKit.SnapshotOptions.NONE, None,
                                     self.shot_done, what[2])
        return False

    def js(self, win, body, then=None):
        def done(view, res):
            try:
                val = view.call_async_javascript_function_finish(res)
                out = json.loads(val.to_json(0) or 'null') if val and not val.is_undefined() else None
            except GLib.Error as e:
                out = {'error': e.message}
            (then or (lambda v: self.step(v)))(out)
        win.web.call_async_javascript_function(body, -1, None, None, 'leaflet://app/test', None, done)

    def until(self, win, body, deadline):
        def got(v):
            if v and not (isinstance(v, dict) and 'error' in v):
                self.step(v)
            elif time.time() > deadline:
                self.step({'timeout': True, 'last': v})
            else:
                GLib.timeout_add(150, lambda: self.js(win, body, got) and False)
        self.js(win, body, got)

    def shot_done(self, view, res, name):
        try:
            tex = view.get_snapshot_finish(res)
            tex.save_to_png(os.path.join(SHOTS, name))
        except GLib.Error as e:
            print('snapshot failed', e.message)
        GLib.timeout_add(50, self.step, None)


def pdf_texts(win):
    return ('js', win, '''
        const d = Leaflet.mode.doc, out = [];
        for (let i = 1; i <= d.numPages; i++) {
          const t = await (await d.getPage(i)).getTextContent();
          out.push(t.items.map(x => x.str).join(''));
        }
        return out;''')


def pss_kb(pid):
    try:
        with open(f'/proc/{pid}/smaps_rollup') as f:
            for line in f:
                if line.startswith('Pss:'):
                    return int(line.split()[1])
    except OSError:
        pass
    return 0


def process_tree(pid):
    out = []
    for k in subprocess.run(['pgrep', '-P', str(pid)], capture_output=True, text=True).stdout.split():
        out.append(int(k))
        out += process_tree(int(k))
    return out


def memory_mb():
    """Proportional memory (PSS) of Leaflet and every process it started."""
    procs = [os.getpid()] + process_tree(os.getpid())
    return sum(pss_kb(p) for p in procs) // 1024, len(procs)


def scenario(app):
    win = app.new_window()
    win.set_default_size(1200, 820)
    yield ('until', win, 'return !!window.Leaflet && document.readyState === "complete"')
    yield ('sleep', 0.5)
    yield ('shot', win, '01-welcome.png')

    # ---------------------------------------------------------------- a PDF
    win.open_path(os.path.join(FILES, 'pages.pdf'))
    r = yield ('until', win, 'return Leaflet.mode && Leaflet.mode.doc && document.querySelectorAll(".thumb canvas").length >= 3 && document.querySelector(".textLayer span") ? Leaflet.mode.doc.numPages : 0')
    check('PDF opens with page thumbnails and a text layer', r == 5, r)
    texts = yield pdf_texts(win)
    check('PDF text can be read (for search and copy)', texts and texts[0] == 'Page 1', texts)
    yield ('shot', win, '02-pdf.png')
    mem, procs = memory_mb()
    check(f'memory with a PDF open: {mem} MB (PSS) in {procs} processes', mem < 450, mem)

    r = yield ('js', win, 'document.querySelector("#find").value = "Page 4"; document.querySelector("#find").dispatchEvent(new Event("input")); return true')
    r = yield ('until', win, 'const t = document.querySelector("#find-count").textContent; return t.includes("of") ? t : ""')
    check('find in a PDF', isinstance(r, str) and r.endswith('of 1'), r)
    r = yield ('until', win, 'return document.querySelector("#page").value === "4"')
    check('find jumps to the page', r is True, r)
    yield ('js', win, 'document.querySelector("#find").value = ""; document.querySelector("#find").dispatchEvent(new Event("input")); return true')

    # page operations, through the same calls the toolbar uses
    r = yield ('js', win, 'const m = Leaflet.mode; m.select([1], true); await m.rotate(90); return (await m.doc.getPage(2)).rotate')
    check('turn a page', r == 90, r)
    r = yield ('js', win, 'const m = Leaflet.mode; m.select([3], true); await m.pageOp("Deleted", (o, b) => o.remove(b, [3])); return m.doc.numPages')
    check('delete a page', r == 4, r)
    r = yield ('js', win, 'const m = Leaflet.mode; await m.moveTo(0, [3]); return m.doc.numPages')
    texts = yield pdf_texts(win)
    check('move a page', texts == ['Page 5', 'Page 1', 'Page 2', 'Page 3'], texts)
    with open(os.path.join(FILES, 'extra.pdf'), 'rb') as f:
        token = win.store.put(f.read())
    r = yield ('js', win, f'await Leaflet.mode.picked({{kind: "pdf", token: "{token}", name: "extra.pdf", at: 1}}); return Leaflet.mode.doc.numPages')
    texts = yield pdf_texts(win)
    check('insert pages from another PDF', texts == ['Page 5', 'Extra 1', 'Extra 2', 'Page 1', 'Page 2', 'Page 3'], texts)
    r = yield ('js', win, 'await Leaflet.mode.undoPages(); return Leaflet.mode.doc.numPages')
    check('undo a page change', r == 4, r)
    r = yield ('until', win, 'return document.querySelectorAll(".thumb canvas").length >= 4')
    yield ('js', win, 'Leaflet.mode.select([0, 1], true); return true')
    yield ('sleep', 0.4)
    yield ('shot', win, '03-pdf-pages-edited.png')

    saved = os.path.join(OUT, 'pages-edited.pdf')
    win.path = saved
    yield ('js', win, 'await Leaflet.mode.save(false); return true')
    r = yield ('until', win, 'return !document.querySelector("#toolbar .save").classList.contains("dirty")')
    data = open(saved, 'rb').read() if os.path.exists(saved) else b''
    check('save a changed PDF', data.startswith(b'%PDF'), len(data))
    check('saved PDF has no author/creator/producer or XMP',
          data and not any(x in data for x in (b'Sample Author', b'Sample Writer', b'Sample Producer', b'/Producer',
                                                b'/Author', b'/Creator', b'pdf-lib', b'xmpmeta')))
    check('deleted page is gone from the saved file', data and b'(Page 4) Tj' not in data)

    # ---------------------------------------------------------------- a form, with annotations
    win2 = app.new_window()
    win2.set_default_size(1200, 820)
    yield ('until', win2, 'return !!window.Leaflet')
    win2.open_path(os.path.join(FILES, 'form.pdf'))
    r = yield ('until', win2, 'return document.querySelector(".annotationLayer input[type=text]") ? true : false')
    check('PDF form fields are shown', r is True, r)
    yield ('js', win2, '''
        const t = document.querySelector(".annotationLayer input[type=text]");
        t.focus(); t.value = "Ada Example"; t.dispatchEvent(new Event("input", {bubbles: true}));
        t.dispatchEvent(new Event("change", {bubbles: true})); t.blur();
        const c = document.querySelector(".annotationLayer input[type=checkbox]");
        c.click(); return true''')
    # a note (free text) and a drawing, made the way a mouse would
    r = yield ('js', win2, '''
        const m = Leaflet.mode; m.setTool("note");
        await new Promise(r => setTimeout(r, 300));
        const layer = document.querySelector(".annotationEditorLayer");
        const box = layer.getBoundingClientRect();
        const opts = (x, y) => ({bubbles: true, cancelable: true, clientX: box.left + x, clientY: box.top + y, button: 0, buttons: 1, pointerId: 1, isPrimary: true, pointerType: "mouse"});
        layer.dispatchEvent(new PointerEvent("pointerdown", opts(120, 330)));
        layer.dispatchEvent(new PointerEvent("pointerup", opts(120, 330)));
        await new Promise(r => setTimeout(r, 400));
        const ed = document.querySelector(".freeTextEditor .internal");
        if (ed) { ed.focus(); document.execCommand("insertText", false, "Checked by the tests"); }
        await new Promise(r => setTimeout(r, 200));
        m.setTool("draw");
        await new Promise(r => setTimeout(r, 300));
        const l2 = document.querySelector(".annotationEditorLayer");
        const b2 = l2.getBoundingClientRect();
        const o2 = (x, y) => ({bubbles: true, cancelable: true, clientX: b2.left + x, clientY: b2.top + y, button: 0, buttons: 1, pointerId: 2, isPrimary: true, pointerType: "mouse"});
        l2.dispatchEvent(new PointerEvent("pointerdown", o2(400, 300)));
        for (let i = 1; i < 12; i++) (document.elementFromPoint(b2.left + 400 + i * 12, b2.top + 300 + (i % 2) * 20) || l2).dispatchEvent(new PointerEvent("pointermove", o2(400 + i * 12, 300 + (i % 2) * 20)));
        (document.elementFromPoint(b2.left + 532, b2.top + 300) || l2).dispatchEvent(new PointerEvent("pointerup", o2(532, 300)));
        await new Promise(r => setTimeout(r, 300));
        m.setTool("select");
        await new Promise(r => setTimeout(r, 300));
        return {note: !!ed, storage: m.doc.annotationStorage.size};''')
    check('add a note and a drawing to a PDF', isinstance(r, dict) and r.get('storage', 0) >= 3, r)
    yield ('sleep', 0.3)
    yield ('shot', win2, '04-pdf-form-annotated.png')
    filled = os.path.join(OUT, 'form-filled.pdf')
    win2.path = filled
    yield ('js', win2, 'await Leaflet.mode.save(false); return true')
    yield ('until', win2, 'return !document.querySelector("#toolbar .save").classList.contains("dirty")')
    yield ('js', win2, 'window.__old = Leaflet.mode.doc; return true')
    win2.kind = None
    win2.dirty = False
    win2.open_path(filled)
    r = yield ('until', win2, '''
        const m = Leaflet.mode; if (!m.doc || m.doc === window.__old) return null;
        const annots = await (await m.doc.getPage(1)).getAnnotations();
        const f = Object.fromEntries(annots.filter(a => a.fieldName).map(a => [a.fieldName, a.fieldValue]));
        return {name: f.name, agree: f.agree, kinds: annots.map(a => a.subtype)};''')
    check('filled-in form values are saved', isinstance(r, dict) and r.get('name') == 'Ada Example', r)
    check('ticked box is saved', isinstance(r, dict) and r.get('agree') not in (None, 'Off'), r)
    check('note and drawing are saved as annotations', isinstance(r, dict) and
          {'FreeText', 'Ink'} <= set(r.get('kinds', [])), r)

    # ---------------------------------------------------------------- a hostile PDF
    before = len(HITS)
    win2.kind = None
    win2.open_path(os.path.join(FILES, 'hostile.pdf'))
    r = yield ('until', win2, 'return Leaflet.mode.doc && document.querySelector(".annotationLayer a") ? true : false')
    yield ('sleep', 3)
    yield ('shot', win2, '05-pdf-hostile.png')
    check('a PDF with JavaScript and web links opens without any network request', len(HITS) == before, HITS[before:])
    r = yield ('js', win2, 'return [typeof window.app, document.title]')
    check('the PDF\'s script never ran', r == ['undefined', 'Leaflet'], r)
    yield ('js', win2, 'document.querySelector(".annotationLayer a[href^=\'http\']").click(); return true')
    yield ('sleep', 0.5)
    check('clicking a web link asks first (and nothing is fetched)',
          any(f'127.0.0.1:{PORT}/link' in u for u in LINKS) and len(HITS) == before, (LINKS, HITS[before:]))

    # ---------------------------------------------------------------- documents
    win3 = app.new_window()
    win3.set_default_size(1200, 820)
    yield ('until', win3, 'return !!window.Leaflet')
    win3.open_path(os.path.join(FILES, 'sample.docx'))
    r = yield ('until', win3, 'return document.body.classList.contains("doc") && document.querySelector("#doc table") ? document.querySelector("#doc h1").textContent : null')
    check('DOCX opens in the editor', r == 'Quarterly notes', r)
    r = yield ('js', win3, 'return [!document.querySelector("#banner").hidden, document.querySelector("#banner-text").textContent]')
    check('DOCX warns about what it can\'t keep', r and r[0] and 'comments' in r[1], r)
    yield ('shot', win3, '06-docx.png')
    # edit: a new bold heading word, a list item, a table row
    r = yield ('js', win3, '''
        const d = document.querySelector("#doc"); const p = d.querySelector("p");
        const r = document.createRange(); r.setStart(p.firstChild, 0); r.collapse(true);
        getSelection().removeAllRanges(); getSelection().addRange(r);
        document.execCommand("insertText", false, "Edited. ");
        r.setStart(p.firstChild, 0); r.setEnd(p.firstChild, 6); getSelection().removeAllRanges(); getSelection().addRange(r);
        await Leaflet.mode.command("bold");
        Leaflet.mode.cell = d.querySelector("td"); Leaflet.mode.addRow(true); Leaflet.mode.changed();
        return d.querySelectorAll("tr").length;''')
    check('edit a document (text, bold, table row)', r == 4, r)
    out_docx = os.path.join(OUT, 'sample-edited.docx')
    win3.path = out_docx
    yield ('js', win3, 'await Leaflet.mode.save(false); return true')
    yield ('until', win3, 'return !document.querySelector("#toolbar .save").classList.contains("dirty")')
    try:
        doc = lf.read_docx(open(out_docx, 'rb').read())
        first = doc.blocks[1].runs
        tables = [b for b in doc.blocks if isinstance(b, lf.Table)]
        imgs = [r for b in doc.blocks if isinstance(b, lf.Para) for r in b.runs if isinstance(r, lf.Image)]
        lists = [b for b in doc.blocks if isinstance(b, lf.ListBlock)]
        check('edited DOCX saves and reads back', first[0].text == 'Edited' and first[0].b and len(tables[0].rows) == 4
              and imgs and len(lists) == 2, [first[:2], len(imgs), len(lists)])
    except Exception as e:  # noqa: BLE001
        check('edited DOCX saves and reads back', False, e)

    exported = os.path.join(OUT, 'sample-export.pdf')
    yield ('js', win3, f'Leaflet.LeafletTest = true; window.webkit.messageHandlers.leaflet.postMessage(JSON.stringify({{cmd: "export-pdf", path: {json.dumps(exported)}}})); return true')
    deadline = time.time() + 30
    while not os.path.exists(exported) and time.time() < deadline:
        yield ('sleep', 0.5)
    yield ('sleep', 0.5)
    data = open(exported, 'rb').read() if os.path.exists(exported) else b''
    check('export a document as PDF', data.startswith(b'%PDF'), len(data))
    check('exported PDF has no creator/producer/dates', data and not any(
        x in data for x in (b'cairo', b'/Producer', b'/Creator', b'/CreationDate', b'WebKit')))

    win3.kind = None
    win3.dirty = False
    win3.open_path(os.path.join(FILES, 'sample.odt'))
    r = yield ('until', win3, 'return document.querySelector("#doc h1") && document.querySelector("#doc h1").textContent === "Trip plan" ? document.querySelectorAll("#doc li").length : null')
    check('ODT opens in the editor', r == 5, r)
    out_odt = os.path.join(OUT, 'sample-edited.odt')
    win3.path = out_odt
    yield ('js', win3, 'document.querySelector("#doc h1").append(" (edited)"); Leaflet.mode.changed(); await Leaflet.mode.save(false); return true')
    yield ('until', win3, 'return !document.querySelector("#toolbar .save").classList.contains("dirty")')
    try:
        doc = lf.read_odt(open(out_odt, 'rb').read())
        check('edited ODT saves and reads back', doc.blocks[0].runs[0].text == 'Trip plan (edited)', doc.blocks[0])
    except Exception as e:  # noqa: BLE001
        check('edited ODT saves and reads back', False, e)
    yield ('shot', win3, '07-odt.png')

    win3.kind = None
    win3.open_path(exported)
    yield ('until', win3, 'return Leaflet.mode.doc && document.querySelectorAll(".thumb canvas").length >= 1')
    yield ('sleep', 0.5)
    yield ('shot', win3, '08-exported-pdf.png')

    # ---------------------------------------------------------------- themes and the new-document screen
    os.environ['LEAFLET_THEME'] = 'classic'
    win.send('state', **win.state())
    yield ('sleep', 0.6)
    yield ('shot', win, '09-classic-theme.png')
    os.environ['LEAFLET_THEME'] = 'dark'
    win3.kind = None
    win3.open_path(os.path.join(FILES, 'sample.docx'))
    win3.send('state', **win3.state())
    yield ('until', win3, 'return document.querySelector("#doc h1") ? true : false')
    yield ('sleep', 0.6)
    yield ('shot', win3, '10-dark-theme.png')
    os.environ['LEAFLET_THEME'] = 'classic'
    win4 = app.new_window(new_doc=True)
    win4.set_default_size(1200, 820)
    r = yield ('until', win4, 'return document.body.classList.contains("doc")')
    yield ('js', win4, 'document.execCommand("insertText", false, "A new letter"); Leaflet.mode.block("h1"); document.execCommand("insertParagraph"); document.execCommand("insertText", false, "Dear reader,"); return true')
    yield ('sleep', 0.4)
    yield ('shot', win4, '11-new-document-classic.png')
    check('a new document can be written', r is True, r)
    r = yield ('js', win4, 'document.querySelector("[data-cmd=menu]").click(); await new Promise(r => setTimeout(r, 200)); return document.querySelectorAll("#menu button").length')
    check('the file menu opens', (r or 0) >= 8, r)
    yield ('shot', win4, '12-menu-classic.png')
    yield ('js', win4, 'document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true})); document.querySelector("[data-cmd=settings]").click(); await new Promise(r => setTimeout(r, 200)); return true')
    r = yield ('js', win4, 'return [!document.querySelector("#modal").hidden, document.querySelector("#set-recent").checked]')
    check('settings open, with recent files off', r == [True, False], r)
    yield ('shot', win4, '13-settings-classic.png')
    yield ('js', win4, 'document.querySelector(".dialog .buttons button").click(); return true')

    check('no network request at any point', not HITS, HITS)
    for w in (win, win2, win3, win4):
        w.dirty = False
        w.closing = True


def main():
    class TestApp(leaflet.LeafletApp):
        def do_activate(self):
            self.hold()
            Driver(self, scenario(self))

    # Links are recorded instead of asking and opening a browser.
    leaflet.LeafletWindow.confirm_link = lambda self, url: LINKS.append(url)
    TestApp().run([])
    failed = [r for r in RESULTS if not r[1]]
    print(f'\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed; screenshots in {SHOTS}')
    print(f'peak memory of the main process: {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024} MB')
    return 1 if failed or not RESULTS else 0


if __name__ == '__main__':
    sys.exit(main())
