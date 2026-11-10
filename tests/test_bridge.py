"""What leaflet.py accepts from the page, and its temporary folders.   python3 -m unittest discover tests

The page may be run by a hostile document, so it never gets to name a file:
these tests send what such a page could send. They need GTK and WebKitGTK
to import leaflet.py (not a screen); without them they're skipped, and
tests/run.sh --live checks the same things in the real window.
"""
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
os.environ.setdefault('GTK_A11Y', 'none')
try:
    import leaflet
except (ImportError, ValueError) as e:      # no gi, or no GTK 4 / WebKitGTK 6
    leaflet = None
    WHY = str(e)

PDF = b'%PDF-1.4\n% a test file\n'


class Window(SimpleNamespace):
    """Just enough of a LeafletWindow to run its command handlers."""

    def __init__(self, **kw):
        self.opened, self.written, self.errors, self.asked = [], [], [], []
        app = SimpleNamespace(settings={}, open_in=lambda win, path, **k: self.opened.append((path, k)),
                              remember=lambda path: None)
        attrs = dict(app=app, store=leaflet.Store(), path=None, kind=None, name='', exports={},
                     recent_shown=[], after_save=None)
        super().__init__(**{**attrs, **kw})
        self.error = self.errors.append
        self.send = lambda *a, **k: None
        self.cmd_title = lambda msg: None
        self.open_path = lambda path, *a: self.opened.append((path, {}))
        self.write = lambda path, data, fmt, msg: self.written.append((path, data, fmt))
        self.ask_where = lambda fmt, suggest, then: self.asked.append((fmt, suggest, then))
        for name in ('export_pdf_to', 'theme'):
            setattr(self, name, getattr(leaflet.LeafletWindow, name).__get__(self))

    def call(self, cmd, **msg):
        getattr(leaflet.LeafletWindow, 'cmd_' + cmd)(self, msg)


@unittest.skipIf(leaflet is None, 'GTK 4 and WebKitGTK 6 are needed to import leaflet.py')
class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='leaflet-test-')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.target = os.path.join(self.dir, 'target')

    def test_open_takes_no_path_from_the_page(self):
        win = Window()
        dialogs = []
        fake = mock.Mock(side_effect=lambda **kw: dialogs.append(kw) or mock.Mock())
        with mock.patch.object(leaflet.Gtk, 'FileDialog', fake):
            win.call('open', path=self.target)
        self.assertEqual(win.opened, [])
        self.assertEqual(len(dialogs), 1)        # it's the user who picks, in the dialog

    def test_recent_files_are_opened_by_number(self):
        win = Window(recent_shown=['/somewhere/a.pdf', '/somewhere/b.odt'])
        for bad in (-1, 2, 99, '0', '/somewhere/a.pdf', 1.0, True, None, [0]):
            win.call('open', recent=bad, path=self.target)
        self.assertEqual(win.opened, [])
        win.call('open', recent=1)
        self.assertEqual(win.opened, [('/somewhere/b.odt', {})])

    def test_state_keeps_the_paths_here(self):
        there = os.path.join(self.dir, 'there.pdf')
        open(there, 'wb').close()
        win = Window()
        win.app.settings.update(remember_recent=True, recent=[there, os.path.join(self.dir, 'gone.pdf'), 5])
        with mock.patch.dict(os.environ, {'LEAFLET_THEME': 'light'}):
            state = leaflet.LeafletWindow.state(win)
        self.assertEqual(state['recent'], [{'name': 'there.pdf', 'folder': self.dir}])
        self.assertEqual(win.recent_shown, [there])
        win.app.settings['remember_recent'] = False
        with mock.patch.dict(os.environ, {'LEAFLET_THEME': 'light'}):
            self.assertEqual(leaflet.LeafletWindow.state(win)['recent'], [])
        self.assertEqual(win.recent_shown, [])

    def test_drop_takes_contents_not_a_path(self):
        win = Window()
        win.call('drop', uri='file://' + self.target)
        self.assertEqual(win.opened, [])
        win.call('drop', token=win.store.put(PDF), name='../../x/.dropped.pdf', uri='file://' + self.target)
        self.assertEqual(win.opened, [(None, {'data': PDF, 'name': 'dropped.pdf'})])

    def test_export_goes_where_the_dialog_said(self):
        win = Window()
        win.call('export_pdf', path=self.target)
        self.assertEqual([(a[0], a[1]) for a in win.asked], [('pdf', None)])     # asks; the path is ignored
        self.assertFalse(os.path.exists(self.target))
        self.assertEqual(os.listdir(self.dir), [])

        win.call('write_export', token=win.store.put(PDF), path=self.target)
        win.call('write_export', token=win.store.put(PDF), path=self.target, ticket=self.target)
        win.call('write_export', token=win.store.put(PDF), ticket=['x'])
        self.assertEqual(win.written, [])

        win.exports['ticket-1'] = '/chosen/in/the/dialog.pdf'
        win.call('write_export', token=win.store.put(b'#!/bin/sh\n'), ticket='ticket-1', path=self.target)
        self.assertEqual(win.written, [])           # not a PDF, and the ticket is used up
        win.exports['ticket-2'] = '/chosen/in/the/dialog.pdf'
        win.call('write_export', token=win.store.put(PDF), ticket='ticket-2', path=self.target)
        win.call('write_export', token=win.store.put(PDF), ticket='ticket-2')
        self.assertEqual(win.written, [('/chosen/in/the/dialog.pdf', PDF, 'pdf')])
        self.assertEqual(win.exports, {})

    def test_save_goes_to_the_open_file_or_asks(self):
        win = Window(path='/docs/open.pdf', kind='pdf')
        win.call('save', token=win.store.put(PDF, 'application/pdf'), format='pdf', path=self.target)
        self.assertEqual(win.written, [('/docs/open.pdf', PDF, 'pdf')])
        win.call('save', token=win.store.put(PDF, 'application/pdf'), format='pdf', path=self.target,
                 **{'as': True, 'suggest': '../../.bashrc'})
        self.assertEqual(len(win.written), 1)
        self.assertEqual([(a[0], a[1]) for a in win.asked], [('pdf', '../../.bashrc')])
        self.assertEqual(leaflet.clean_name('../../.bashrc'), 'bashrc')
        self.assertEqual(leaflet.clean_name('/etc/cron.d/x\n.pdf'), 'x.pdf')
        self.assertEqual(leaflet.clean_name('..\\..\\evil.docx'), 'evil.docx')
        self.assertEqual(leaflet.clean_name(''), 'Untitled')
        self.assertEqual(leaflet.clean_name({'a': 1}, 'file'), "{'a': 1}")

    def test_save_refuses_the_wrong_kind_of_data(self):
        win = Window(path='/docs/open.docx', kind='docx')
        for data, kind, fmt in ((b'#!/bin/sh\n', 'application/octet-stream', 'docx'),    # raw bytes as a document
                                (b'#!/bin/sh\n', 'application/pdf', 'pdf'),              # not a PDF
                                (b'<p>x</p>', 'text/html', 'pdf'),
                                (b'<p>x</p>', 'text/html', 'sh'), (b'<p>x</p>', 'text/html', ['docx'])):
            try:
                win.call('save', token=win.store.put(data, kind), format=fmt)
            except TypeError:
                pass                                                                     # an unhashable format
        self.assertEqual(win.written, [])
        self.assertEqual(win.asked, [])
        win.call('save', token=win.store.put(b'<p>x</p>', 'text/html'), format='docx')
        self.assertEqual([(w[0], w[2]) for w in win.written], [('/docs/open.docx', 'docx')])
        self.assertTrue(win.written[0][1].startswith(b'PK'))

    def test_messages_that_are_not_commands_are_ignored(self):
        win = Window()
        for text in ('[1, 2]', '"open"', '{"cmd": ["open"]}', '{"cmd": 5}', 'null', '{'):
            leaflet.LeafletWindow.on_message(win, None, SimpleNamespace(to_string=lambda t=text: t))
        self.assertEqual((win.opened, win.errors), ([], []))


@unittest.skipIf(leaflet is None, 'GTK 4 and WebKitGTK 6 are needed to import leaflet.py')
class TempDirsTest(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix='leaflet-test-')
        self.addCleanup(shutil.rmtree, self.base, True)
        patch = mock.patch.object(tempfile, 'tempdir', self.base)
        patch.start()
        self.addCleanup(patch.stop)

    def test_private_and_removed(self):
        temp = leaflet.TempDirs()
        a, b = temp.make(), temp.make()
        self.assertEqual(stat.S_IMODE(os.stat(a).st_mode), 0o700)
        self.assertEqual(os.path.dirname(a), self.base)
        with open(os.path.join(a, 'print.pdf'), 'wb') as f:
            f.write(PDF)
        os.mkdir(os.path.join(b, 'sub'))
        temp.remove(a)
        self.assertFalse(os.path.exists(a))
        temp.clear()                                # what runs when Leaflet quits
        self.assertEqual(os.listdir(self.base), [])
        temp.remove(a)                              # twice is fine

    def test_stale_folders_of_a_killed_leaflet(self):
        temp = leaflet.TempDirs()
        old, fresh, ours = (tempfile.mkdtemp(prefix=temp.PREFIX) for _ in range(3))
        other = tempfile.mkdtemp(prefix='something-else-')
        temp.dirs.add(ours)
        long_ago = time.time() - 3 * 24 * 3600
        for path in (old, ours, other):
            os.utime(path, (long_ago, long_ago))
        temp.remove_stale()
        self.assertEqual(sorted(os.listdir(self.base)), sorted(os.path.basename(p) for p in (fresh, ours, other)))

    def test_print_leaves_nothing_when_it_fails(self):
        win = Window(name='report.pdf')
        with mock.patch.object(leaflet, 'TEMP', leaflet.TempDirs()), \
                mock.patch.object(leaflet.Gtk, 'PrintDialog', side_effect=RuntimeError('no printing here')):
            with self.assertRaises(RuntimeError):
                win.call('print', token=win.store.put(PDF))
            self.assertEqual((os.listdir(self.base), leaflet.TEMP.dirs), ([], set()))

    def test_print_cleans_up_when_the_dialog_is_done(self):
        win = Window(name='../report.pdf')
        seen = {}

        class Dialog:
            def __init__(self, **kw):
                pass

            def print_file(self, _win, _setup, file, _cancel, done):
                seen['path'] = file.get_path()
                with open(file.get_path(), 'rb') as f:
                    seen['data'] = f.read()
                seen['done'] = done

            def print_file_finish(self, _res):
                raise leaflet.GLib.Error('the printer is on fire')
        with mock.patch.object(leaflet, 'TEMP', leaflet.TempDirs()), mock.patch.object(leaflet.Gtk, 'PrintDialog', Dialog):
            win.call('print', token=win.store.put(PDF))
            self.assertEqual(seen['data'], PDF)
            self.assertEqual(os.path.basename(seen['path']), 'report.pdf')
            self.assertEqual(os.path.dirname(os.path.dirname(seen['path'])), self.base)
            seen['done'](Dialog(), None)
            self.assertEqual(os.listdir(self.base), [])
            self.assertEqual(len(win.errors), 1)

    def test_export_leaves_nothing_when_it_fails(self):
        win = Window(web=None)
        with mock.patch.object(leaflet, 'TEMP', leaflet.TempDirs()), \
                mock.patch.object(leaflet.WebKit.PrintOperation, 'new', side_effect=RuntimeError('no web view')):
            with self.assertRaises(RuntimeError):
                win.export_pdf_to(os.path.join(self.base, 'out.pdf'))
            self.assertEqual((os.listdir(self.base), leaflet.TEMP.dirs), ([], set()))


if __name__ == '__main__':
    unittest.main()
