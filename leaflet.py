#!/usr/bin/env python3
"""Leaflet — a light, private app for PDFs and word-processor documents.

A GTK 4 window around a WebKit view; the interface lives in ui/. PDFs are
shown with PDF.js (bundled, without its JavaScript engine, so a PDF's own
scripts never run) and changed with pdf-lib. DOCX, ODT, RTF and text files
are converted by leaflet_formats.py.

Nothing in the window can reach the network: the page is served from a
private leaflet:// scheme, its Content-Security-Policy only allows that
scheme, and the web view's proxy is a closed port.

The page never names a file. Which file is read or written is decided here:
by a file dialog, the command line, a file dropped on the window, the open
document's own path, or the recent list, which the page refers to by number.

Usage: leaflet.py [FILE ...]
"""
import atexit
import json
import os
import shutil
import signal
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('WebKit', '6.0')
from gi.repository import Gdk, Gio, GLib, Gtk, WebKit  # noqa: E402

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))
import leaflet_formats as formats  # noqa: E402

APP_ID = 'app.leaflet.Leaflet'
UI_DIR = APP_DIR / 'ui'
# The only file Leaflet writes on its own. Recent files are kept in it only
# when they're switched on in the settings (they're off to begin with).
CONFIG_DIR = Path(GLib.get_user_config_dir()) / 'leaflet'
SETTINGS_FILE = CONFIG_DIR / 'settings.json'
MAX_FILE = 512 * 1024 * 1024
MAX_RECENT = 10
# A closed port: if anything in the page ever tried to reach the network,
# it would go here and fail. (The tests point it at a listener instead.)
BLACKHOLE_PROXY = os.environ.get('LEAFLET_TEST_PROXY') or 'http://127.0.0.1:9'

MIME = {'html': 'text/html', 'js': 'text/javascript', 'mjs': 'text/javascript', 'css': 'text/css',
        'svg': 'image/svg+xml', 'png': 'image/png', 'gif': 'image/gif', 'json': 'application/json',
        'wasm': 'application/wasm', 'bcmap': 'application/octet-stream', 'pfb': 'application/octet-stream',
        'ttf': 'font/ttf', 'icc': 'application/octet-stream'}

FORMAT_NAMES = {'pdf': 'PDF', 'docx': 'Word document', 'odt': 'OpenDocument text', 'rtf': 'Rich Text',
                'txt': 'Plain text'}
SAVE_FILTERS = {'pdf': ('PDF', ['*.pdf']), 'docx': ('Word document (.docx)', ['*.docx']),
                'odt': ('OpenDocument text (.odt)', ['*.odt']), 'txt': ('Plain text (.txt)', ['*.txt'])}
THEME_BG = {'light': '#e9eaee', 'dark': '#25262b', 'classic': '#c0c0c0'}


def low_memory():
    """Under 2.5 GB of RAM, WebKit's compositing costs more than it gives."""
    if os.environ.get('LEAFLET_LOW_MEMORY'):
        return os.environ['LEAFLET_LOW_MEMORY'] != '0'
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    return int(line.split()[1]) < 2_500_000
    except (OSError, ValueError):
        pass
    return False


def load_settings():
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_atomic(path, data: bytes):
    path = Path(os.path.realpath(path))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, os.stat(path).st_mode & 0o777)
        except OSError:
            os.chmod(tmp, 0o644 & ~current_umask())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def current_umask():
    mask = os.umask(0)
    os.umask(mask)
    return mask


def save_settings(settings):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    write_atomic(SETTINGS_FILE, json.dumps(settings, indent=1, ensure_ascii=False).encode())


def read_limited(path):
    with open(path, 'rb') as f:
        data = f.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise formats.FormatError('the file is larger than 512 MB')
    return data


class TempDirs:
    """Private folders (0700) for a file on its way to the printer or out of an
    export. Each is removed as soon as it's done with, and whatever is left
    goes when Leaflet quits, however it quits."""
    PREFIX = 'leaflet-tmp-'

    def __init__(self):
        self.dirs = set()
        atexit.register(self.clear)

    def make(self):
        path = tempfile.mkdtemp(prefix=self.PREFIX)
        self.dirs.add(path)
        return path

    def remove(self, path):
        self.dirs.discard(path)
        shutil.rmtree(path, ignore_errors=True)

    def clear(self):
        for path in list(self.dirs):
            self.remove(path)

    def remove_stale(self, age=24 * 3600):
        """Folders a killed or crashed Leaflet left behind (a day old, so not
        one that another running Leaflet is using)."""
        base = tempfile.gettempdir()
        try:
            names = os.listdir(base)
        except OSError:
            return
        for name in names:
            path = os.path.join(base, name)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if (name.startswith(self.PREFIX) and os.path.isdir(path) and not os.path.islink(path)
                    and st.st_uid == os.getuid() and time.time() - st.st_mtime > age and path not in self.dirs):
                shutil.rmtree(path, ignore_errors=True)


TEMP = TempDirs()


def clean_name(name, fallback='Untitled'):
    """A file name the page suggested, as a plain name: no folders, nothing odd."""
    name = os.path.basename(str(name or '').replace('\\', '/'))
    name = ''.join(c for c in name if c.isprintable()).strip().lstrip('.')[:120]
    return name or fallback


class Store:
    """Bytes handed between Python and the page, each fetched once by a token."""

    def __init__(self):
        self.items = {}

    def put(self, data, kind='application/octet-stream'):
        token = uuid.uuid4().hex
        self.items[token] = (data, kind)
        return token

    def take(self, token):
        return self.items.pop(token, (None, None))


class LeafletWindow(Gtk.ApplicationWindow):
    def __init__(self, app, pending=None):
        super().__init__(application=app)
        self.app = app
        self.path = None            # file on disk, if any
        self.kind = None            # pdf, docx, odt, rtf, txt, or None
        self.dirty = False
        self.name = ''
        self.closing = False
        self.store = Store()
        self.pending = pending      # ('path', p), ('data', bytes, name) or ('new',)
        self.ready = False
        self.after_save = None
        self.recent_shown = []      # the recent files as last sent to the page; it picks one by number
        self.exports = {}           # ticket -> where an export in progress goes (chosen in a dialog)

        size = app.settings.get('_window', {})
        self.set_default_size(size.get('w', 1100), size.get('h', 800))
        if size.get('max'):
            self.maximize()
        self.set_icon_name('leaflet')
        self.set_title('Leaflet')

        manager = WebKit.UserContentManager()
        manager.register_script_message_handler('leaflet', None)
        manager.connect('script-message-received::leaflet', self.on_message)
        session = WebKit.NetworkSession.new_ephemeral()
        session.set_proxy_settings(WebKit.NetworkProxyMode.CUSTOM,
                                   WebKit.NetworkProxySettings.new(BLACKHOLE_PROXY, None))
        self.web = WebKit.WebView(user_content_manager=manager, network_session=session,
                                  web_context=app.web_context)
        ws = self.web.get_settings()
        ws.set_enable_developer_extras(bool(os.environ.get('LEAFLET_DEBUG')))
        ws.set_enable_write_console_messages_to_stdout(bool(os.environ.get('LEAFLET_DEBUG')))
        ws.set_enable_back_forward_navigation_gestures(False)
        ws.set_javascript_can_access_clipboard(True)
        ws.set_enable_page_cache(False)
        ws.set_enable_html5_database(False)
        ws.set_enable_html5_local_storage(False)
        ws.set_enable_webaudio(False)
        ws.set_enable_webgl(False)
        ws.set_enable_media(False)
        ws.set_enable_media_stream(False)
        ws.set_enable_webrtc(False)
        ws.set_allow_file_access_from_file_urls(False)
        ws.set_allow_universal_access_from_file_urls(False)
        ws.set_auto_load_images(True)       # only data: and leaflet: images can load at all
        ws.set_enable_encrypted_media(False)
        ws.set_media_playback_requires_user_gesture(True)
        if low_memory():
            ws.set_hardware_acceleration_policy(WebKit.HardwareAccelerationPolicy.NEVER)
        ws.set_user_agent('Leaflet')
        theme = self.theme()
        color = Gdk.RGBA()
        color.parse(THEME_BG.get(theme, THEME_BG['light']))
        self.web.set_background_color(color)
        self.web.connect('decide-policy', self.on_decide_policy)
        self.web.connect('context-menu', self.on_context_menu)
        self.web.connect('permission-request', lambda _v, req: (req.deny(), True)[1])
        self.web.connect('web-process-terminated', self.on_crash)
        self.web.connect('create', lambda *_: None)
        # Files dragged in from a file manager are taken here, by GTK, so the
        # page never has to be believed about a path.
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect('drop', self.on_drop)
        self.web.add_controller(drop)
        self.set_child(self.web)
        self.connect('close-request', self.on_close_request)
        self.web.load_uri('leaflet://app/index.html')

    # ---------- talking to the page

    def theme(self):
        theme = os.environ.get('LEAFLET_THEME') or self.app.settings.get('theme', 'auto')
        if theme == 'auto':
            dark = Gtk.Settings.get_default().get_property('gtk-application-prefer-dark-theme')
            name = (Gtk.Settings.get_default().get_property('gtk-theme-name') or '').lower()
            theme = 'dark' if dark or name.endswith('-dark') else 'light'
        return theme if theme in THEME_BG else 'light'

    def send(self, what, **payload):
        payload['what'] = what
        script = f'window.Leaflet && window.Leaflet.handle({json.dumps(payload)})'
        self.web.evaluate_javascript(script, -1, None, None, None, None, None)

    def state(self):
        s = self.app.settings
        recent = s.get('recent', []) if s.get('remember_recent') else []
        self.recent_shown = [p for p in recent if isinstance(p, str) and os.path.exists(p)]
        # The page gets names to show; the paths stay here.
        return {'theme': self.theme(), 'themeSetting': os.environ.get('LEAFLET_THEME') or s.get('theme', 'auto'),
                'rememberRecent': bool(s.get('remember_recent')),
                'recent': [{'name': os.path.basename(p), 'folder': os.path.dirname(p)} for p in self.recent_shown]}

    def on_message(self, _manager, value):
        try:
            msg = json.loads(value.to_string())
        except (ValueError, TypeError):
            return
        if not isinstance(msg, dict) or not isinstance(msg.get('cmd'), str):
            return
        cmd = msg['cmd']
        handler = getattr(self, f'cmd_{cmd}'.replace('-', '_'), None)
        if handler:
            try:
                handler(msg)
            except Exception as e:  # noqa: BLE001 — the window must stay usable
                self.error(f'Something went wrong: {e}')

    def error(self, text):
        self.send('error', text=text)

    # ---------- commands from the page

    def cmd_ready(self, _msg):
        self.ready = True
        self.send('state', **self.state())
        pending, self.pending = self.pending, None
        if pending and pending[0] == 'path':
            self.open_path(pending[1])
        elif pending and pending[0] == 'data':
            self.open_path(None, pending[1], pending[2])
        elif pending and pending[0] == 'new':
            self.cmd_new({})

    def cmd_title(self, msg):
        self.dirty = bool(msg.get('dirty'))
        name = self.name or 'Untitled'
        self.set_title(f'{"• " if self.dirty else ""}{name} — Leaflet')

    def cmd_open(self, msg):
        if 'recent' in msg:         # a file in the recent list, by its place in it
            n = msg['recent']
            if isinstance(n, int) and not isinstance(n, bool) and 0 <= n < len(self.recent_shown):
                self.app.open_in(self, self.recent_shown[n])
            return
        dialog = Gtk.FileDialog(title='Open', modal=True)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        all_docs = Gtk.FileFilter(name='PDFs and documents')
        for pat in ('*.pdf', '*.docx', '*.odt', '*.rtf', '*.txt', '*.PDF', '*.DOCX', '*.ODT', '*.RTF', '*.TXT'):
            all_docs.add_pattern(pat)
        filters.append(all_docs)
        everything = Gtk.FileFilter(name='All files')
        everything.add_pattern('*')
        filters.append(everything)
        dialog.set_filters(filters)
        if self.path:
            dialog.set_initial_folder(Gio.File.new_for_path(os.path.dirname(self.path)))

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.app.open_in(self, f.get_path())
        dialog.open(self, None, done)

    def cmd_new(self, _msg):
        if self.kind is not None:
            self.app.new_window(new_doc=True)
            return
        self.kind, self.path, self.name = 'docx', None, ''
        self.send('load', kind='doc', name='Untitled', format='docx', saveAs=True,
                  html='<p><br></p>', warnings=[])
        self.cmd_title({'dirty': False})

    def cmd_drop(self, msg):
        """A dropped file that reached the page: it sends the contents, never a
        path. (Files dropped from a file manager are taken by on_drop below.)"""
        data, _ = self.store.take(msg.get('token', ''))
        if data is not None:
            self.app.open_in(self, None, data=data, name=clean_name(msg.get('name'), 'Dropped file'))

    def on_drop(self, _target, value, _x, _y):
        """Files dropped on the window, straight from GTK: the page isn't asked."""
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        for path in paths[:8]:
            self.app.open_in(self, path)
        return bool(paths)

    def cmd_pick(self, msg):
        """A file to put into the current one: a PDF to merge, or a picture."""
        what = msg.get('for')
        dialog = Gtk.FileDialog(title='Insert pages from a PDF' if what == 'pdf' else 'Insert a picture', modal=True)
        flt = Gtk.FileFilter(name='PDF' if what == 'pdf' else 'Pictures')
        for pat in (['*.pdf', '*.PDF'] if what == 'pdf' else
                    ['*.png', '*.jpg', '*.jpeg', '*.gif', '*.bmp', '*.PNG', '*.JPG', '*.JPEG', '*.GIF', '*.BMP']):
            flt.add_pattern(pat)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(flt)
        dialog.set_filters(filters)

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            try:
                data = read_limited(f.get_path())
            except (OSError, formats.FormatError) as e:
                self.error(f'Couldn\'t read that file: {e}')
                return
            if what == 'pdf' and formats.detect(data) != 'pdf':
                self.error('That file isn\'t a PDF.')
                return
            if what == 'image':
                mime = formats.sniff_image(data)
                if not mime:
                    self.error('Leaflet can insert PNG, JPEG, GIF and BMP pictures.')
                    return
                if len(data) > formats.MAX_IMAGE:
                    self.error('That picture is too large (over 32 MB).')
                    return
            token = self.store.put(data)
            self.send('picked', token=token, kind=what, name=os.path.basename(f.get_path()),
                      at=msg.get('at'))
        dialog.open(self, None, done)

    def cmd_save(self, msg):
        """The page put the finished file in the store; write it out."""
        token = msg.get('token', '')
        fmt = msg.get('format') or self.kind
        data, kind = self.store.take(token)
        if data is None:
            self.error('Nothing to save.')
            return
        # A document is saved as a document, and only a PDF as a PDF.
        if fmt not in SAVE_FILTERS or (fmt == 'pdf') != (kind != 'text/html') or \
                (fmt == 'pdf' and formats.detect(data) != 'pdf'):
            self.error('Leaflet can\'t save that.')
            return
        try:
            if kind == 'text/html':
                doc = formats.from_html(data.decode('utf-8'))
                data = formats.save(doc, fmt)
        except formats.FormatError as e:
            self.error(str(e))
            return
        same_format = fmt == self.kind or (fmt == 'pdf' and self.kind == 'pdf')
        if self.path and not msg.get('as') and same_format:
            self.write(self.path, data, fmt, msg)
            return
        self.ask_where(fmt, msg.get('suggest'), lambda path: self.write(path, data, fmt, msg))

    # Where a file goes is only ever chosen here, in a dialog; the page can
    # suggest a name for it and nothing more.

    def ask_where(self, fmt, suggest, then):
        dialog = Gtk.FileDialog(title='Save as' if fmt != 'pdf' or self.kind == 'pdf' else 'Export as PDF',
                                modal=True)
        label, pats = SAVE_FILTERS[fmt]
        flt = Gtk.FileFilter(name=label)
        for p in pats:
            flt.add_pattern(p)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(flt)
        dialog.set_filters(filters)
        base = clean_name(suggest or self.name)
        dialog.set_initial_name(f'{os.path.splitext(base)[0] or "Untitled"}.{fmt}')
        if self.path:
            dialog.set_initial_folder(Gio.File.new_for_path(os.path.dirname(self.path)))

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            path = f.get_path() if f else None
            if not path:
                return
            if path.lower().endswith('.' + fmt):
                then(path)
                return
            # The name chosen had no extension: the dialog asked about
            # replacing that name, not this one, so ask here.
            path += '.' + fmt
            if os.path.lexists(path):
                self.confirm_replace(path, then)
            else:
                then(path)
        dialog.save(self, None, done)

    def confirm_replace(self, path, then):
        name = os.path.basename(path)
        dialog = Gtk.AlertDialog(message=f'{name} already exists. Replace it?',
                                 detail=f'It\'s in {os.path.dirname(path)}. Replacing it overwrites what\'s there.',
                                 buttons=['Cancel', 'Replace'], cancel_button=0, default_button=0, modal=True)

        def done(d, res):
            try:
                if d.choose_finish(res) == 1:
                    then(path)
            except GLib.Error:
                pass
        dialog.choose(self, None, done)

    def write(self, path, data, fmt, msg):
        try:
            write_atomic(path, data)
        except OSError as e:
            self.error(f'Couldn\'t save: {e.strerror or e}')
            return
        export = msg.get('export')
        if not export:
            self.path, self.kind, self.name = path, fmt, os.path.basename(path)
            self.app.remember(path)
        self.send('saved', name=os.path.basename(path), export=bool(export), format=fmt)
        if not export:
            self.cmd_title({'dirty': False})
        if self.after_save and not export:
            then, self.after_save = self.after_save, None
            then()

    def cmd_export_pdf(self, _msg):
        """Print the document to a PDF; the page then strips its metadata and saves it."""
        self.ask_where('pdf', None, self.export_pdf_to)

    def export_pdf_to(self, target):
        tmpdir = TEMP.make()
        try:
            out = os.path.join(tmpdir, 'export.pdf')
            settings = Gtk.PrintSettings()
            settings.set_printer('Print to File')
            settings.set(Gtk.PRINT_SETTINGS_OUTPUT_FILE_FORMAT, 'pdf')
            settings.set(Gtk.PRINT_SETTINGS_OUTPUT_URI, Gio.File.new_for_path(out).get_uri())
            setup = Gtk.PageSetup()
            setup.set_paper_size(Gtk.PaperSize.new(Gtk.PAPER_NAME_A4))
            for side in ('top', 'bottom', 'left', 'right'):
                getattr(setup, f'set_{side}_margin')(20 if side in ('top', 'bottom') else 22, Gtk.Unit.MM)
            op = WebKit.PrintOperation.new(self.web)
            op.set_print_settings(settings)
            op.set_page_setup(setup)

            def finished(_op):
                try:
                    data = read_limited(out)
                except (OSError, formats.FormatError) as e:
                    self.error(f'Couldn\'t export: {getattr(e, "strerror", None) or e}')
                    return
                finally:
                    TEMP.remove(tmpdir)
                # The page gets the printed file and a ticket; the ticket, not
                # the page, says where the cleaned file is written.
                ticket = uuid.uuid4().hex
                self.exports[ticket] = target
                self.send('exported', token=self.store.put(data), ticket=ticket)

            def failed(_op, err):
                TEMP.remove(tmpdir)
                self.error(f'Couldn\'t export: {err.message}')
            op.connect('finished', finished)
            op.connect('failed', failed)
            op.print_()
        except BaseException:
            TEMP.remove(tmpdir)
            raise

    def cmd_write_export(self, msg):
        target = self.exports.pop(msg.get('ticket'), None) if isinstance(msg.get('ticket'), str) else None
        data, _ = self.store.take(msg.get('token', ''))
        if target is None or data is None or formats.detect(data) != 'pdf':
            return
        self.write(target, data, 'pdf', {'export': True})

    def cmd_print(self, msg):
        data, _ = self.store.take(msg.get('token', ''))
        if data is None:        # a document: print the page as it's laid out
            WebKit.PrintOperation.new(self.web).run_dialog(self)
            return
        # A PDF: hand the file itself to the print dialog, from a private folder
        # that goes away again whatever happens to the print job.
        tmpdir = TEMP.make()

        def done(dialog, res):
            try:
                dialog.print_file_finish(res)
            except GLib.Error as e:
                if not e.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                    self.error(f'Couldn\'t print: {e.message}')
            finally:
                TEMP.remove(tmpdir)
        try:
            path = os.path.join(tmpdir, os.path.splitext(clean_name(self.name, 'document'))[0] + '.pdf')
            with open(path, 'wb') as f:
                f.write(data)
            Gtk.PrintDialog(modal=True).print_file(self, None, Gio.File.new_for_path(path), None, done)
        except BaseException:
            TEMP.remove(tmpdir)
            raise

    def cmd_link(self, msg):
        url = msg.get('url') or ''
        if not url.lower().startswith(('http://', 'https://', 'mailto:')):
            return
        self.confirm_link(url)

    def confirm_link(self, url):
        shown = url if len(url) <= 300 else url[:300] + '…'
        dialog = Gtk.AlertDialog(message='Open this link in your web browser?', detail=shown,
                                 buttons=['Cancel', 'Open link'], cancel_button=0, default_button=0, modal=True)

        def done(d, res):
            try:
                if d.choose_finish(res) == 1:
                    Gtk.UriLauncher.new(url).launch(self, None, None, None)
            except GLib.Error:
                pass
        dialog.choose(self, None, done)

    def cmd_settings(self, msg):
        s = self.app.settings
        if msg.get('theme') in ('auto', 'light', 'dark', 'classic'):
            s['theme'] = msg['theme']
        if 'rememberRecent' in msg:
            s['remember_recent'] = bool(msg['rememberRecent'])
            if not s['remember_recent']:
                s.pop('recent', None)
        if msg.get('clearRecent'):
            s.pop('recent', None)
        self.app.save_settings()
        for win in self.app.get_windows():
            if isinstance(win, LeafletWindow):
                color = Gdk.RGBA()
                color.parse(THEME_BG[win.theme()])
                win.web.set_background_color(color)
                win.send('state', **win.state())

    def cmd_close(self, _msg):
        self.closing = True
        self.close()

    def cmd_quit(self, _msg):
        self.app.ask_quit()

    def cmd_fullscreen(self, _msg):
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    # ---------- opening files

    def open_path(self, path, data=None, name=None):
        try:
            if data is None:
                data = read_limited(path)
            kind = formats.detect(data, path or name or '')
            if kind is None:
                raise formats.FormatError('Leaflet can\'t open this kind of file.')
            if kind == 'ole':
                raise formats.FormatError('This is an old binary Word file (.doc). Leaflet opens .docx, .odt, '
                                          '.rtf, .txt and PDF files.')
        except (OSError, formats.FormatError) as e:
            self.error(f'Couldn\'t open {os.path.basename(path or name or "the file")}: '
                       f'{getattr(e, "strerror", None) or e}')
            return False
        self.path = path
        self.kind = kind
        self.name = os.path.basename(path) if path else (name or 'Untitled')
        if path:
            self.app.remember(path)
        if kind == 'pdf':
            token = self.store.put(data, 'application/pdf')
            self.send('load', kind='pdf', name=self.name, token=token, saveAs=not path)
        else:
            try:
                doc = formats.load(data, kind)
            except formats.FormatError as e:
                self.kind = None
                self.error(f'Couldn\'t open {self.name}: {e}')
                return False
            # RTF and text can be read but are saved as DOCX (or text).
            save_as = not path or kind == 'rtf'
            self.send('load', kind='doc', name=self.name, format=kind, saveAs=save_as,
                      html=formats.to_html(doc), warnings=doc.warnings)
        self.cmd_title({'dirty': False})
        return True

    # ---------- the leaflet:// scheme

    def serve(self, req):
        path = unquote(urlsplit(req.get_uri()).path)
        if path.startswith('/blob/'):
            data, kind = self.store.take(path[6:])
            if data is None:
                return 404, b'', 'text/plain'
            return 200, data, kind
        if path == '/put' and req.get_http_method() == 'POST':
            body = req.get_http_body()
            chunks = []
            total = 0
            if body is not None:
                while True:
                    chunk = body.read_bytes(1 << 20, None).get_data()
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_FILE:
                        return 413, b'too large', 'text/plain'
                    chunks.append(chunk)
            kind = (req.get_http_headers().get_one('Content-Type') or 'application/octet-stream').split(';')[0]
            token = self.store.put(b''.join(chunks), kind)
            return 200, token.encode(), 'text/plain'
        return None

    # ---------- web view policy

    def on_decide_policy(self, _view, decision, kind):
        if kind in (WebKit.PolicyDecisionType.NAVIGATION_ACTION, WebKit.PolicyDecisionType.NEW_WINDOW_ACTION):
            action = decision.get_navigation_action()
            uri = action.get_request().get_uri()
            if kind == WebKit.PolicyDecisionType.NAVIGATION_ACTION and uri.startswith('leaflet://app/index.html'):
                decision.use()
                return True
            decision.ignore()
            if action.is_user_gesture() and uri.lower().startswith(('http://', 'https://', 'mailto:')):
                self.confirm_link(uri)
            return True
        if kind == WebKit.PolicyDecisionType.RESPONSE:
            if not decision.is_main_frame_main_resource() or decision.get_request().get_uri().startswith('leaflet://'):
                decision.use()
            else:
                decision.ignore()
            return True
        return False

    def on_context_menu(self, _view, menu, _hit):
        keep = {WebKit.ContextMenuAction.COPY, WebKit.ContextMenuAction.CUT, WebKit.ContextMenuAction.PASTE,
                WebKit.ContextMenuAction.SELECT_ALL, WebKit.ContextMenuAction.DELETE,
                WebKit.ContextMenuAction.SPELLING_GUESS, WebKit.ContextMenuAction.NO_GUESSES_FOUND,
                WebKit.ContextMenuAction.IGNORE_SPELLING, WebKit.ContextMenuAction.LEARN_SPELLING,
                WebKit.ContextMenuAction.UNICODE, WebKit.ContextMenuAction.INPUT_METHODS,
                WebKit.ContextMenuAction.PASTE_AS_PLAIN_TEXT}
        if os.environ.get('LEAFLET_DEBUG'):
            keep.add(WebKit.ContextMenuAction.INSPECT_ELEMENT)
        for item in list(menu.get_items()):
            if item.get_stock_action() not in keep:
                menu.remove(item)
        return menu.get_n_items() == 0

    def on_crash(self, _view, _reason):
        self.exports.clear()
        self.web.load_uri('leaflet://app/index.html')
        if self.path:
            self.pending = ('path', self.path)
        GLib.idle_add(lambda: self.error('The document view stopped and was restarted. '
                                         'Unsaved changes were lost.') and False)

    def on_close_request(self, _win):
        if self.dirty and not self.closing:
            self.ask_unsaved(lambda: (setattr(self, 'closing', True), self.close()))
            return True
        self.app.store_window(self)
        self.exports.clear()
        return False

    def ask_unsaved(self, then):
        dialog = Gtk.AlertDialog(message=f'Save changes to {self.name or "Untitled"}?',
                                 detail='Your changes will be lost if you don\'t save them.',
                                 buttons=['Cancel', 'Don\'t save', 'Save'], cancel_button=0, default_button=2,
                                 modal=True)

        def done(d, res):
            try:
                choice = d.choose_finish(res)
            except GLib.Error:
                return
            if choice == 1:
                then()
            elif choice == 2:
                self.after_save = then
                self.send('save-request')
        self.present()
        dialog.choose(self, None, done)


class LeafletApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN |
                         (Gio.ApplicationFlags.NON_UNIQUE if os.environ.get('LEAFLET_TEST') else 0))
        self.settings = load_settings()
        self.web_context = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        GLib.set_application_name('Leaflet')
        Gtk.Window.set_default_icon_name('leaflet')
        # Files opened here don't go into the desktop's "Recent files" list.
        Gtk.Settings.get_default().set_property('gtk-recent-files-enabled', False)
        self.web_context = WebKit.WebContext()
        self.web_context.set_cache_model(WebKit.CacheModel.DOCUMENT_VIEWER)
        self.web_context.register_uri_scheme('leaflet', self.on_scheme)
        sec = self.web_context.get_security_manager()
        sec.register_uri_scheme_as_secure('leaflet')
        sec.register_uri_scheme_as_cors_enabled('leaflet')
        # Asked to stop: quit the ordinary way, so nothing is left in the temporary folder.
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: self.quit() or GLib.SOURCE_REMOVE)
        if not os.environ.get('LEAFLET_TEST'):
            TEMP.remove_stale()

    def do_shutdown(self):
        TEMP.clear()
        Gtk.Application.do_shutdown(self)
        # Keyboard shortcuts are handled by the page (ui/app.js).

    def ask_quit(self):
        for win in self.get_windows():
            if isinstance(win, LeafletWindow) and win.dirty and not win.closing:
                win.ask_unsaved(lambda w=win: (setattr(w, 'closing', True), w.close(), self.ask_quit()))
                return
        for win in self.get_windows():
            if isinstance(win, LeafletWindow):
                win.closing = True
                win.close()

    def do_activate(self):
        self.new_window()

    def do_open(self, files, _n, _hint):
        for f in files:
            path = f.get_path()
            if path:
                self.new_window(path)
            elif f.get_uri():
                self.new_window()

    def new_window(self, path=None, new_doc=False, data=None, name=None):
        pending = ('path', path) if path else ('data', data, name) if data is not None else \
            ('new',) if new_doc else None
        win = LeafletWindow(self, pending)
        win.present()
        return win

    def open_in(self, win, path, data=None, name=None):
        """Open a file in this window if it's empty, otherwise in a new one."""
        if win.kind is None and not win.dirty:
            win.open_path(path, data, name)
        else:
            self.new_window(path, data=data, name=name)

    def on_scheme(self, req):
        view = req.get_web_view()
        win = next((w for w in self.get_windows() if isinstance(w, LeafletWindow) and w.web is view), None)
        result = win.serve(req) if win else None
        if result is None:
            result = self.static(req)
        status, data, kind = result
        if status != 200:
            req.finish_error(GLib.Error.new_literal(Gio.io_error_quark(), f'{status}', Gio.IOErrorEnum.NOT_FOUND))
            return
        stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))
        resp = WebKit.URISchemeResponse.new(stream, len(data))
        resp.set_content_type(kind)
        req.finish_with_response(resp)

    @staticmethod
    def static(req):
        parts = urlsplit(req.get_uri())
        if parts.netloc != 'app':
            return 404, b'', 'text/plain'
        rel = os.path.normpath(unquote(parts.path)).lstrip('/')
        target = (UI_DIR / rel).resolve()
        if UI_DIR.resolve() not in target.parents or not target.is_file():
            return 404, b'', 'text/plain'
        return 200, target.read_bytes(), MIME.get(target.suffix.lstrip('.'), 'application/octet-stream')

    def remember(self, path):
        if not self.settings.get('remember_recent'):
            return
        recent = [p for p in self.settings.get('recent', []) if p != path]
        self.settings['recent'] = [path] + recent[:MAX_RECENT - 1]
        self.save_settings()

    def store_window(self, win):
        if os.environ.get('LEAFLET_TEST'):
            return
        w, h = win.get_default_size()
        self.settings['_window'] = {'w': w, 'h': h, 'max': win.is_maximized()}
        self.save_settings()

    def save_settings(self):
        if os.environ.get('LEAFLET_TEST'):
            return
        try:
            save_settings(self.settings)
        except OSError:
            pass


def main():
    if os.environ.get('LEAFLET_TEST'):
        os.environ.setdefault('GTK_A11Y', 'none')
    app = LeafletApp()
    return app.run(sys.argv)


if __name__ == '__main__':
    sys.exit(main())
