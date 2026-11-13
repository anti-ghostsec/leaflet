"""Reading and writing word-processor documents for Leaflet.

DOCX, ODT, RTF and plain text are turned into one small document model, and
that model into the HTML the editor shows (and back). Only what the model
knows survives: paragraphs, headings, bold/italic/underline/strike,
super/subscript, alignment, links, lists, simple tables, pictures and page
breaks. Everything else (comments, tracked changes, headers and footers,
footnotes, text boxes...) is reported as a warning when the file is opened.

Written files carry no author, dates, program name or other metadata, the
zip entries all have the same fixed timestamp, and pictures are written
without their EXIF, XMP, IPTC, comments and text (see strip_image_metadata).

Only the Python standard library is used. Files are untrusted: zip members
are size-checked before they're read, XML with a DOCTYPE is refused, and the
HTML that goes to the editor is built here from the model, never copied.
"""
import base64
import html
import io
import re
import zipfile
import zlib
from dataclasses import dataclass, field
from html.parser import HTMLParser
from xml.etree import ElementTree as ET
from xml.parsers import expat

MAX_MEMBER = 64 * 1024 * 1024       # one file inside a document
MAX_TOTAL = 256 * 1024 * 1024       # everything inside a document
MAX_IMAGE = 32 * 1024 * 1024
EMU_PER_PX = 9525                   # 914400 EMU per inch, 96 px per inch
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

IMAGE_TYPES = {  # mime -> file extension
    'image/png': 'png', 'image/jpeg': 'jpeg', 'image/gif': 'gif', 'image/bmp': 'bmp',
}


class FormatError(Exception):
    """The file can't be read as the format it claims to be."""


_DOCTYPE_REFUSED = 'the document contains a DOCTYPE, which Leaflet refuses to read'


# ---------------------------------------------------------------- the model

@dataclass
class Text:
    text: str
    b: bool = False
    i: bool = False
    u: bool = False
    s: bool = False
    sup: bool = False
    sub: bool = False
    href: str | None = None

    def same_format(self, other):
        return (isinstance(other, Text) and
                (self.b, self.i, self.u, self.s, self.sup, self.sub, self.href) ==
                (other.b, other.i, other.u, other.s, other.sup, other.sub, other.href))


@dataclass
class Image:
    data: bytes
    mime: str
    width: int = 0      # px, 0 = natural size
    height: int = 0
    alt: str = ''


@dataclass
class Break:
    pass


@dataclass
class Para:
    runs: list = field(default_factory=list)
    style: str = 'p'            # p, h1..h6
    align: str = ''             # '', center, right, justify


@dataclass
class ListBlock:
    ordered: bool
    items: list = field(default_factory=list)   # each item: list of blocks


@dataclass
class Cell:
    blocks: list = field(default_factory=list)
    colspan: int = 1


@dataclass
class Table:
    rows: list = field(default_factory=list)    # each row: list of Cell


@dataclass
class PageBreak:
    pass


@dataclass
class Document:
    blocks: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def merge_runs(runs):
    """Join neighbouring text runs that look the same."""
    out = []
    for r in runs:
        if isinstance(r, Text):
            if not r.text:
                continue
            if out and r.same_format(out[-1]):
                out[-1] = Text(out[-1].text + r.text, r.b, r.i, r.u, r.s, r.sup, r.sub, r.href)
                continue
        out.append(r)
    return out


def plain_text(blocks):
    lines = []
    for b in blocks:
        if isinstance(b, Para):
            lines.append(''.join(r.text if isinstance(r, Text) else ('\n' if isinstance(r, Break) else '')
                                 for r in b.runs))
        elif isinstance(b, ListBlock):
            for n, item in enumerate(b.items, 1):
                sub = plain_text(item).split('\n') or ['']
                mark = f'{n}. ' if b.ordered else '• '
                lines.append(mark + sub[0])
                lines.extend('   ' + s for s in sub[1:])
        elif isinstance(b, Table):
            for row in b.rows:
                lines.append('\t'.join(plain_text(c.blocks).replace('\n', ' ') for c in row))
        elif isinstance(b, PageBreak):
            lines.append('\f')
    return '\n'.join(lines)


# ---------------------------------------------------------------- safe input

def open_zip(data: bytes):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError) as e:
        raise FormatError('not a valid zip-based document') from e
    total = 0
    for info in z.infolist():
        if info.file_size > MAX_MEMBER:
            raise FormatError(f'{info.filename} is too large')
        total += info.file_size
    if total > MAX_TOTAL:
        raise FormatError('the document unpacks to more than 256 MB')
    # The sizes above are what the zip says; read_member counts what is
    # really unpacked, since a forged header can say anything.
    z.leaflet_unpacked = 0
    return z


def read_member(z, name, required=True):
    try:
        info = z.getinfo(name)
    except KeyError:
        if required:
            raise FormatError(f'{name} is missing') from None
        return None
    so_far = getattr(z, 'leaflet_unpacked', 0)
    room = min(MAX_MEMBER, MAX_TOTAL - so_far)
    with z.open(info) as f:
        data = f.read(room + 1)   # the size in the header could lie
    z.leaflet_unpacked = so_far + len(data)
    if len(data) > MAX_MEMBER:
        raise FormatError(f'{name} is too large')
    if len(data) > room:
        raise FormatError('the document unpacks to more than 256 MB')
    return data


def parse_xml(data: bytes):
    """Parse XML that has no DOCTYPE: no DTD means no entities to expand.

    The parser itself refuses the declarations, so it doesn't matter how the
    file is encoded (UTF-16 would hide them from a search through the bytes).
    """
    if re.search(rb'<!(DOCTYPE|ENTITY)', data, re.I):
        raise FormatError(_DOCTYPE_REFUSED)
    builder = ET.TreeBuilder()
    parser = expat.ParserCreate(namespace_separator='}')
    parser.buffer_text = True

    def name(n):
        return '{' + n if '}' in n else n

    def refuse(*_):
        raise FormatError(_DOCTYPE_REFUSED)

    parser.StartElementHandler = lambda tag, attrs: builder.start(name(tag), {name(k): v for k, v in attrs.items()})
    parser.EndElementHandler = lambda tag: builder.end(name(tag))
    parser.CharacterDataHandler = builder.data
    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.UnparsedEntityDeclHandler = refuse
    parser.NotationDeclHandler = refuse
    try:
        parser.Parse(data, True)
        return builder.close()
    except (expat.ExpatError, ET.ParseError, AssertionError, LookupError, ValueError) as e:
        raise FormatError(f'broken XML: {e}') from e


def sniff_image(data: bytes):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if data.startswith(b'BM'):
        return 'image/bmp'
    return None


def detect(data: bytes, name: str = ''):
    """What kind of file this is, by its contents (the name only breaks ties)."""
    if data[:1024].lstrip().startswith(b'%PDF') or b'%PDF-' in data[:1024]:
        return 'pdf'
    if data.startswith(b'PK'):
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
            names = set(z.namelist())
        except (zipfile.BadZipFile, ValueError):
            return None
        if 'word/document.xml' in names:
            return 'docx'
        if 'mimetype' in names and 'content.xml' in names:
            mt = read_member(z, 'mimetype', False) or b''
            if mt.strip() == b'application/vnd.oasis.opendocument.text':
                return 'odt'
        return None
    if data.lstrip()[:5] == b'{\\rtf':
        return 'rtf'
    if data.startswith(b'\xd0\xcf\x11\xe0'):
        return 'ole'    # old binary .doc
    if b'\x00' in data[:8192] and not data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return None
    return 'txt'


def load(data: bytes, kind: str) -> Document:
    if kind == 'docx':
        return read_docx(data)
    if kind == 'odt':
        return read_odt(data)
    if kind == 'rtf':
        return read_rtf(data)
    if kind == 'txt':
        return read_txt(data)
    raise FormatError('Leaflet can\'t open this kind of file')


def save(doc: Document, kind: str) -> bytes:
    if kind == 'docx':
        return write_docx(doc)
    if kind == 'odt':
        return write_odt(doc)
    if kind == 'txt':
        return plain_text(doc.blocks).encode('utf-8')
    raise FormatError(f'Leaflet can\'t save as {kind}')


# ---------------------------------------------------------------- DOCX in

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
WP = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
PIC = 'http://schemas.openxmlformats.org/drawingml/2006/picture'
V = 'urn:schemas-microsoft-com:vml'
PR = 'http://schemas.openxmlformats.org/package/2006/relationships'
MC = 'http://schemas.openxmlformats.org/markup-compatibility/2006'


def w(tag):
    return f'{{{W}}}{tag}'


def wattr(el, name, default=None):
    if el is None:
        return default
    return el.get(w(name), default)


def on_off(el):
    """<w:b/> is on, <w:b w:val="0"/> is off, missing is None (inherit)."""
    if el is None:
        return None
    return wattr(el, 'val', 'true') not in ('0', 'false', 'off', 'none')


def rels_of(z, part):
    folder, _, name = part.rpartition('/')
    data = read_member(z, f'{folder}/_rels/{name}.rels', False)
    out = {}
    if data:
        for rel in parse_xml(data):
            target = rel.get('Target', '')
            external = rel.get('TargetMode') == 'External'
            if not external and not target.startswith('/'):
                target = f'{folder}/{target}'
            out[rel.get('Id')] = (target.lstrip('/'), external, rel.get('Type', ''))
    return out


class _DocxStyles:
    def __init__(self, root):
        self.styles = {}
        self.heading = {}
        if root is None:
            return
        for st in root.iter(w('style')):
            sid = wattr(st, 'styleId')
            name_el = st.find(w('name'))
            name = (wattr(name_el, 'val') or '').lower()
            self.styles[sid] = st
            m = re.fullmatch(r'heading ([1-6])', name)
            if m:
                self.heading[sid] = int(m.group(1))
            elif name == 'title':
                self.heading[sid] = 1
            elif name == 'subtitle':
                self.heading[sid] = 2
            else:
                ol = st.find(f"{w('pPr')}/{w('outlineLvl')}")
                if ol is not None and wattr(ol, 'val', '9').isdigit() and int(wattr(ol, 'val')) < 6:
                    self.heading[sid] = int(wattr(ol, 'val')) + 1

    def chain(self, sid):
        seen = set()
        while sid and sid in self.styles and sid not in seen:
            seen.add(sid)
            yield self.styles[sid]
            sid = wattr(self.styles[sid].find(w('basedOn')), 'val')

    def heading_level(self, sid):
        for st in self.chain(sid):
            if wattr(st, 'styleId') in self.heading:
                return self.heading[wattr(st, 'styleId')]
        return 0

    def run_props(self, sid):
        props = {}
        for st in self.chain(sid):
            rpr = st.find(w('rPr'))
            if rpr is not None:
                for k, v in _rpr(rpr).items():
                    props.setdefault(k, v)
        return props

    def numpr(self, sid):
        for st in self.chain(sid):
            n = st.find(f"{w('pPr')}/{w('numPr')}")
            if n is not None:
                return n
        return None


def _rpr(rpr):
    props = {}
    for key, tag in (('b', 'b'), ('i', 'i'), ('s', 'strike')):
        v = on_off(rpr.find(w(tag)))
        if v is not None:
            props[key] = v
    u = rpr.find(w('u'))
    if u is not None:
        props['u'] = wattr(u, 'val', 'single') != 'none'
    va = rpr.find(w('vertAlign'))
    if va is not None:
        props['sup'] = wattr(va, 'val') == 'superscript'
        props['sub'] = wattr(va, 'val') == 'subscript'
    return props


class _DocxNumbering:
    def __init__(self, root):
        self.abstract = {}
        self.nums = {}
        if root is None:
            return
        for an in root.findall(w('abstractNum')):
            levels = {}
            for lvl in an.findall(w('lvl')):
                levels[int(wattr(lvl, 'ilvl', '0'))] = wattr(lvl.find(w('numFmt')), 'val', 'decimal')
            self.abstract[wattr(an, 'abstractNumId')] = levels
        for num in root.findall(w('num')):
            self.nums[wattr(num, 'numId')] = wattr(num.find(w('abstractNumId')), 'val')

    def ordered(self, num_id, ilvl):
        fmt = self.abstract.get(self.nums.get(num_id), {}).get(ilvl, 'decimal')
        return fmt not in ('bullet', 'none')


class _DocxReader:
    def __init__(self, data):
        self.z = open_zip(data)
        names = set(self.z.namelist())
        ct = read_member(self.z, '[Content_Types].xml', False) or b''
        self.main = 'word/document.xml'
        m = re.search(rb'PartName="/([^"]+)"[^>]*ContentType="application/vnd\.openxmlformats-officedocument'
                      rb'\.wordprocessingml\.document\.main\+xml"', ct)
        if m and m.group(1).decode() in names:
            self.main = m.group(1).decode()
        self.rels = rels_of(self.z, self.main)
        self.styles = _DocxStyles(self._part('styles'))
        self.numbering = _DocxNumbering(self._part('numbering'))
        self.warnings = set()
        self.images = {}

    def _part(self, kind):
        for target, external, typ in self.rels.values():
            if not external and typ.endswith('/' + kind):
                data = read_member(self.z, target, False)
                return parse_xml(data) if data else None
        return None

    def warn(self, text):
        self.warnings.add(text)

    def read(self):
        root = parse_xml(read_member(self.z, self.main))
        body = root.find(w('body'))
        if body is None:
            raise FormatError('the document has no body')
        if body.find(f".//{w('headerReference')}") is not None or body.find(f".//{w('footerReference')}") is not None:
            self.warn('headers and footers')
        doc = Document(self.blocks(body))
        doc.warnings = sorted(self.warnings)
        return doc

    # -- blocks

    def blocks(self, parent):
        items = []      # (kind, payload): ('block', b) or ('li', (numId, ilvl, ordered, para))
        for el in self.children(parent):
            if el.tag == w('p'):
                items.extend(self.paragraph(el))
            elif el.tag == w('tbl'):
                items.append(('block', self.table(el)))
        return self.group_lists(items)

    def children(self, parent):
        """Block children, looking through content controls and tracked insertions."""
        for el in parent:
            if el.tag in (w('sdt'),):
                content = el.find(w('sdtContent'))
                if content is not None:
                    yield from self.children(content)
            elif el.tag in (w('customXml'), w('ins'), w('moveTo')):
                yield from self.children(el)
            elif el.tag == f'{{{MC}}}AlternateContent':
                fb = el.find(f'{{{MC}}}Fallback')
                if fb is not None:
                    yield from self.children(fb)
            else:
                yield el

    def paragraph(self, p):
        ppr = p.find(w('pPr'))
        sid = wattr(ppr.find(w('pStyle')), 'val') if ppr is not None else None
        level = self.styles.heading_level(sid) if sid else 0
        if ppr is not None and level == 0:
            ol = ppr.find(w('outlineLvl'))
            if ol is not None and (wattr(ol, 'val') or '9').isdigit() and int(wattr(ol, 'val')) < 6:
                level = int(wattr(ol, 'val')) + 1
        para = Para(style=f'h{level}' if level else 'p')
        jc = wattr(ppr.find(w('jc')), 'val') if ppr is not None else None
        para.align = {'center': 'center', 'right': 'right', 'end': 'right', 'both': 'justify',
                      'distribute': 'justify'}.get(jc, '')
        base = self.styles.run_props(sid) if sid and not level else {}
        out = []
        page_break_before = ppr is not None and on_off(ppr.find(w('pageBreakBefore')))
        if page_break_before:
            out.append(('block', PageBreak()))
        runs, breaks_after = self.inline(p, base, None)
        # A page break inside a paragraph splits it in two.
        pieces = [[]]
        for r in runs:
            if isinstance(r, PageBreak):
                pieces.append([])
            else:
                pieces[-1].append(r)
        numpr = ppr.find(w('numPr')) if ppr is not None else None
        if numpr is None and sid:
            numpr = self.styles.numpr(sid)
        num_id = wattr(numpr.find(w('numId')), 'val') if numpr is not None else None
        ilvl = int(wattr(numpr.find(w('ilvl')), 'val', '0') or 0) if numpr is not None else 0
        for n, piece in enumerate(pieces):
            if n:
                out.append(('block', PageBreak()))
            q = Para(merge_runs(piece), para.style, para.align)
            if num_id and num_id != '0' and n == 0:
                out.append(('li', (num_id, min(ilvl, 8), self.numbering.ordered(num_id, ilvl), q)))
            elif q.runs or len(pieces) == 1:
                out.append(('block', q))
        return out

    def inline(self, parent, base, href):
        runs = []
        for el in parent:
            tag = el.tag
            if tag == w('r'):
                runs.extend(self.run(el, base, href))
            elif tag == w('hyperlink'):
                rid = el.get(f'{{{R}}}id')
                target = self.rels.get(rid)
                link = target[0] if target and target[1] else href
                if link and not re.match(r'(?i)(https?|mailto):', link):
                    link = None
                runs.extend(self.inline(el, base, link)[0])
            elif tag in (w('ins'), w('moveTo'), w('smartTag'), w('customXml'), w('fldSimple'), w('sdtContent')):
                if tag in (w('ins'), w('moveTo')):
                    self.warn('tracked changes (shown as accepted)')
                runs.extend(self.inline(el, base, href)[0])
            elif tag == w('sdt'):
                content = el.find(w('sdtContent'))
                if content is not None:
                    runs.extend(self.inline(content, base, href)[0])
            elif tag in (w('del'), w('moveFrom')):
                self.warn('tracked changes (shown as accepted)')
            elif tag == w('commentRangeStart'):
                self.warn('comments')
            elif tag == f'{{{MC}}}AlternateContent':
                fb = el.find(f'{{{MC}}}Fallback')
                if fb is not None:
                    runs.extend(self.inline(fb, base, href)[0])
            elif tag.startswith('{http://schemas.openxmlformats.org/officeDocument/2006/math}'):
                self.warn('equations')
        return runs, None

    def run(self, r, base, href):
        props = dict(base)
        rpr = r.find(w('rPr'))
        if rpr is not None:
            rs = wattr(rpr.find(w('rStyle')), 'val')
            if rs:
                for k, v in self.styles.run_props(rs).items():
                    props[k] = v
            props.update(_rpr(rpr))
        fmt = dict(b=props.get('b', False), i=props.get('i', False), u=props.get('u', False),
                   s=props.get('s', False), sup=props.get('sup', False), sub=props.get('sub', False), href=href)
        if href:
            fmt['u'] = False    # links are underlined by being links
        out = []
        for el in r:
            tag = el.tag
            if tag == w('t'):
                out.append(Text(el.text or '', **fmt))
            elif tag == w('tab') or tag == w('ptab'):
                out.append(Text('\t', **fmt))
            elif tag in (w('br'), w('cr')):
                out.append(PageBreak() if wattr(el, 'type') == 'page' else Break())
            elif tag == w('noBreakHyphen'):
                out.append(Text('\u2011', **fmt))
            elif tag == w('sym'):
                ch = wattr(el, 'char')
                if ch:
                    try:
                        out.append(Text(chr(int(ch, 16)), **fmt))
                    except ValueError:
                        pass
            elif tag == w('drawing'):
                out.extend(self.drawing(el))
            elif tag == w('pict') or tag == w('object'):
                out.extend(self.vml(el))
            elif tag == f'{{{MC}}}AlternateContent':
                fb = el.find(f'{{{MC}}}Fallback')
                choice = el.find(f'{{{MC}}}Choice')
                found = []
                for alt in (choice, fb):
                    if alt is not None and not found:
                        for d in alt.iter(w('drawing')):
                            found.extend(self.drawing(d))
                        for d in alt.iter(w('pict')):
                            found.extend(self.vml(d))
                out.extend(found)
            elif tag == w('footnoteReference') or tag == w('endnoteReference'):
                self.warn('footnotes and endnotes')
            elif tag == w('commentReference'):
                self.warn('comments')
        return out

    def image_from_rel(self, rid, width=0, height=0, alt=''):
        target = self.rels.get(rid)
        if not target:
            return []
        if target[1]:
            self.warn('pictures linked from the internet (not loaded)')
            return []
        data = read_member(self.z, target[0], False)
        if not data or len(data) > MAX_IMAGE:
            return []
        mime = sniff_image(data)
        if not mime:
            self.warn('pictures in formats Leaflet can\'t show (EMF/WMF/SVG)')
            return []
        return [Image(data, mime, width, height, alt)]

    def drawing(self, d):
        if d.find(f'.//{{{WP}}}txbxContent') is not None or d.find('.//{http://schemas.microsoft.com/office/word/2010/wordprocessingShape}txbx') is not None:
            self.warn('text boxes and shapes')
        if d.find('.//{http://schemas.openxmlformats.org/drawingml/2006/chart}chart') is not None:
            self.warn('charts')
        out = []
        ext = d.find(f'.//{{{WP}}}extent')
        width = int(int(ext.get('cx', '0')) / EMU_PER_PX) if ext is not None else 0
        height = int(int(ext.get('cy', '0')) / EMU_PER_PX) if ext is not None else 0
        doc_pr = d.find(f'.//{{{WP}}}docPr')
        alt = (doc_pr.get('descr') or '') if doc_pr is not None else ''
        if d.find(f'.//{{{WP}}}anchor') is not None:
            self.warn('floating pictures (placed in the text instead)')
        for blip in d.iter(f'{{{A}}}blip'):
            rid = blip.get(f'{{{R}}}embed')
            if blip.get(f'{{{R}}}link') and not rid:
                self.warn('pictures linked from the internet (not loaded)')
            if rid:
                out.extend(self.image_from_rel(rid, width, height, alt))
        return out

    def vml(self, el):
        out = []
        if el.find(f'.//{{{V}}}textbox') is not None:
            self.warn('text boxes and shapes')
        for img in el.iter(f'{{{V}}}imagedata'):
            rid = img.get(f'{{{R}}}id')
            if rid:
                width = height = 0
                shape = None
                for sh in el.iter(f'{{{V}}}shape'):
                    shape = sh
                    break
                if shape is not None:
                    st = dict(kv.split(':', 1) for kv in (shape.get('style') or '').split(';') if ':' in kv)
                    width = css_length_px(st.get('width', ''))
                    height = css_length_px(st.get('height', ''))
                out.extend(self.image_from_rel(rid, width, height))
        return out

    def table(self, tbl):
        t = Table()
        for tr in self.children(tbl):
            if tr.tag != w('tr'):
                continue
            row = []
            for tc in self.children(tr):
                if tc.tag != w('tc'):
                    continue
                tcpr = tc.find(w('tcPr'))
                span = int(wattr(tcpr.find(w('gridSpan')), 'val', '1') or 1) if tcpr is not None else 1
                vm = tcpr.find(w('vMerge')) if tcpr is not None else None
                cell = Cell(colspan=max(1, min(span, 64)))
                if vm is not None and wattr(vm, 'val', 'continue') == 'continue':
                    self.warn('table cells merged across rows (split again)')
                    cell.blocks = [Para()]
                else:
                    cell.blocks = self.blocks(tc) or [Para()]
                row.append(cell)
            if row:
                t.rows.append(row)
        return t

    @staticmethod
    def group_lists(items):
        """Turn runs of numbered paragraphs into (nested) lists."""
        out = []
        i = 0
        while i < len(items):
            kind, payload = items[i]
            if kind == 'block':
                out.append(payload)
                i += 1
                continue
            j = i
            while j < len(items) and items[j][0] == 'li':
                j += 1
            out.extend(_nest([p for _, p in items[i:j]]))
            i = j
        return out


def _nest(entries):
    """entries: (list id, level, ordered, para) -> list blocks, nested by level."""
    blocks = []
    stack = []      # (level, ListBlock, list id)
    for num_id, level, ordered, para in entries:
        while stack and stack[-1][0] > level:
            stack.pop()
        if stack and stack[-1][0] == level and (stack[-1][1].ordered != ordered or
                                                (level == 0 and num_id != stack[-1][2])):
            stack.pop()
        if not stack or stack[-1][0] < level:
            lst = ListBlock(ordered)
            if stack:
                parent = stack[-1][1]
                if not parent.items:
                    parent.items.append([])
                parent.items[-1].append(lst)
            else:
                blocks.append(lst)
            stack.append((level, lst, num_id))
        stack[-1][1].items.append([para])
    return blocks


def css_length_px(value):
    m = re.match(r'\s*([0-9.]+)\s*(pt|px|in|cm|mm|pc|em)?', value or '')
    if not m:
        return 0
    n = float(m.group(1))
    unit = m.group(2) or 'px'
    factor = {'px': 1, 'pt': 96 / 72, 'in': 96, 'cm': 96 / 2.54, 'mm': 96 / 25.4, 'pc': 16, 'em': 16}[unit]
    return int(n * factor)


def read_docx(data: bytes) -> Document:
    return _DocxReader(data).read()


# ---------------------------------------------------------------- DOCX out

def _x(text):
    return html.escape(text, quote=True)


DOCX_CONTENT_TYPES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="png" ContentType="image/png"/>
<Default Extension="jpeg" ContentType="image/jpeg"/>
<Default Extension="gif" ContentType="image/gif"/>
<Default Extension="bmp" ContentType="image/bmp"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
</Types>'''

DOCX_ROOT_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''

_HEADING_PT = {1: 20, 2: 16, 3: 14, 4: 12, 5: 11, 6: 11}


def _docx_styles():
    heads = ''.join(
        f'<w:style w:type="paragraph" w:styleId="Heading{n}"><w:name w:val="heading {n}"/>'
        f'<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:uiPriority w:val="9"/><w:qFormat/>'
        f'<w:pPr><w:keepNext/><w:spacing w:before="{240 if n < 3 else 160}" w:after="80"/>'
        f'<w:outlineLvl w:val="{n - 1}"/></w:pPr>'
        f'<w:rPr><w:b/><w:sz w:val="{_HEADING_PT[n] * 2}"/></w:rPr></w:style>' for n in range(1, 7))
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{W}">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Calibri"/>
<w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
{heads}
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/>
<w:pPr><w:spacing w:after="40"/><w:contextualSpacing/></w:pPr></w:style>
<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont"><w:name w:val="Default Paragraph Font"/></w:style>
<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/><w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style>
<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/>
<w:tblPr><w:tblCellMar><w:left w:w="108" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>
<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:basedOn w:val="TableNormal"/>
<w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>
<w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/><w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/>
<w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/><w:right w:val="single" w:sz="4" w:space="0" w:color="auto"/>
<w:insideH w:val="single" w:sz="4" w:space="0" w:color="auto"/><w:insideV w:val="single" w:sz="4" w:space="0" w:color="auto"/></w:tblBorders></w:tblPr></w:style>
</w:styles>'''


def _docx_numbering(lists):
    """lists: list of (num id, ordered)."""
    def levels(ordered):
        out = []
        for n in range(9):
            ind = 720 * (n + 1)
            if ordered:
                fmt = ['decimal', 'lowerLetter', 'lowerRoman'][n % 3]
                out.append(f'<w:lvl w:ilvl="{n}"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/>'
                           f'<w:lvlText w:val="%{n + 1}."/><w:lvlJc w:val="left"/>'
                           f'<w:pPr><w:ind w:left="{ind}" w:hanging="360"/></w:pPr></w:lvl>')
            else:
                ch = ['\u2022', '\u25e6', '\u25aa'][n % 3]
                out.append(f'<w:lvl w:ilvl="{n}"><w:start w:val="1"/><w:numFmt w:val="bullet"/>'
                           f'<w:lvlText w:val="{ch}"/><w:lvlJc w:val="left"/>'
                           f'<w:pPr><w:ind w:left="{ind}" w:hanging="360"/></w:pPr></w:lvl>')
        return ''.join(out)
    abstracts = (f'<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/>{levels(False)}</w:abstractNum>'
                 f'<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="hybridMultilevel"/>{levels(True)}</w:abstractNum>')
    nums = ''.join(
        f'<w:num w:numId="{nid}"><w:abstractNumId w:val="{1 if ordered else 0}"/>'
        + (''.join(f'<w:lvlOverride w:ilvl="{n}"><w:startOverride w:val="1"/></w:lvlOverride>' for n in range(9))
           if ordered else '') + '</w:num>'
        for nid, ordered in lists)
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:numbering xmlns:w="{W}">{abstracts}{nums}</w:numbering>'


class _DocxWriter:
    def __init__(self):
        self.rels = []          # (id, type, target, external)
        self.media = []         # (name, data)
        self.lists = []         # (num id, ordered)
        self.pic_id = 0

    def rel(self, typ, target, external=False):
        rid = f'rId{len(self.rels) + 10}'
        self.rels.append((rid, typ, target, external))
        return rid

    def write(self, doc):
        body = ''.join(self.block(b) for b in doc.blocks) or '<w:p/>'
        body += ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                 '<w:pgMar w:top="1417" w:right="1417" w:bottom="1134" w:left="1417" w:header="708" w:footer="708" w:gutter="0"/>'
                 '</w:sectPr>')
        document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    f'<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:wp="{WP}" xmlns:a="{A}" xmlns:pic="{PIC}">'
                    f'<w:body>{body}</w:body></w:document>')
        rels = ''.join(
            f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{typ}" '
            f'Target="{_x(target)}"{" TargetMode=\"External\"" if ext else ""}/>'
            for rid, typ, target, ext in
            [('rId1', 'styles', 'styles.xml', False), ('rId2', 'numbering', 'numbering.xml', False)] + self.rels)
        files = [
            ('[Content_Types].xml', DOCX_CONTENT_TYPES),
            ('_rels/.rels', DOCX_ROOT_RELS),
            ('word/document.xml', document),
            ('word/styles.xml', _docx_styles()),
            ('word/numbering.xml', _docx_numbering(self.lists)),
            ('word/_rels/document.xml.rels',
             f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="{PR}">{rels}</Relationships>'),
        ] + [(f'word/media/{name}', data) for name, data in self.media]
        return make_zip(files)

    def block(self, b, numbering=None):
        if isinstance(b, Para):
            return self.para(b, numbering)
        if isinstance(b, ListBlock):
            return self.list(b, 0)
        if isinstance(b, Table):
            return self.table(b)
        if isinstance(b, PageBreak):
            return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
        return ''

    def list(self, lst, level):
        nid = len(self.lists) + 1
        self.lists.append((nid, lst.ordered))
        out = []
        for item in lst.items:
            first = True
            for b in item:
                if isinstance(b, ListBlock):
                    out.append(self.list(b, min(level + 1, 8)))
                elif isinstance(b, Para) and first:
                    out.append(self.para(b, (nid, level)))
                    first = False
                elif isinstance(b, Para):
                    out.append(self.para(b, None, indent=720 * (level + 1)))
                else:
                    out.append(self.block(b))
            if first:
                out.append(self.para(Para(), (nid, level)))
        return ''.join(out)

    def para(self, p, numbering=None, indent=0):
        ppr = ''
        if p.style.startswith('h') and p.style[1:].isdigit():
            ppr += f'<w:pStyle w:val="Heading{p.style[1:]}"/>'
        elif numbering:
            ppr += '<w:pStyle w:val="ListParagraph"/>'
        if numbering:
            ppr += f'<w:numPr><w:ilvl w:val="{numbering[1]}"/><w:numId w:val="{numbering[0]}"/></w:numPr>'
        if indent:
            ppr += f'<w:ind w:left="{indent}"/>'
        if p.align:
            ppr += f'<w:jc w:val="{ {"justify": "both"}.get(p.align, p.align) }"/>'
        out = [f'<w:p>{"<w:pPr>" + ppr + "</w:pPr>" if ppr else ""}']
        i = 0
        runs = p.runs
        while i < len(runs):
            r = runs[i]
            if isinstance(r, Text) and r.href:
                j = i
                while j < len(runs) and isinstance(runs[j], Text) and runs[j].href == r.href:
                    j += 1
                rid = self.rel('hyperlink', r.href, True)
                out.append(f'<w:hyperlink r:id="{rid}">' + ''.join(self.run(x) for x in runs[i:j]) + '</w:hyperlink>')
                i = j
                continue
            out.append(self.run(r))
            i += 1
        out.append('</w:p>')
        return ''.join(out)

    def run(self, r):
        if isinstance(r, Break):
            return '<w:r><w:br/></w:r>'
        if isinstance(r, Image):
            return self.image(r)
        if not isinstance(r, Text):
            return ''
        rpr = ''
        if r.href:
            rpr += '<w:rStyle w:val="Hyperlink"/>'
        if r.b:
            rpr += '<w:b/><w:bCs/>'
        if r.i:
            rpr += '<w:i/><w:iCs/>'
        if r.s:
            rpr += '<w:strike/>'
        if r.u:
            rpr += '<w:u w:val="single"/>'
        if r.sup or r.sub:
            rpr += f'<w:vertAlign w:val="{"superscript" if r.sup else "subscript"}"/>'
        parts = []
        for k, chunk in enumerate(r.text.split('\t')):
            if k:
                parts.append('<w:tab/>')
            if chunk:
                parts.append(f'<w:t xml:space="preserve">{_x(_xml_safe(chunk))}</w:t>')
        return f'<w:r>{"<w:rPr>" + rpr + "</w:rPr>" if rpr else ""}{"".join(parts)}</w:r>'

    def image(self, im):
        ext = IMAGE_TYPES.get(im.mime)
        if not ext:
            return ''
        self.pic_id += 1
        name = f'image{self.pic_id}.{ext}'
        self.media.append((name, strip_image_metadata(im.data, im.mime)))
        rid = self.rel('image', f'media/{name}')
        wpx, hpx = im.width or image_size(im.data)[0] or 200, im.height or image_size(im.data)[1] or 150
        if wpx > 624:   # keep within the page's text width (6.5 in)
            hpx = int(hpx * 624 / wpx)
            wpx = 624
        cx, cy = wpx * EMU_PER_PX, hpx * EMU_PER_PX
        n = self.pic_id
        return (f'<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
                f'<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{n}" name="Picture {n}" descr="{_x(im.alt)}"/>'
                f'<a:graphic><a:graphicData uri="{PIC}"><pic:pic><pic:nvPicPr><pic:cNvPr id="{n}" name="{name}"/>'
                f'<pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch>'
                f'</pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
                f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>'
                f'</wp:inline></w:drawing></w:r>')

    def table(self, t):
        cols = max((sum(c.colspan for c in row) for row in t.rows), default=1)
        width = 9000 // max(cols, 1)
        out = [f'<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/>'
               f'<w:tblLook w:val="04A0"/></w:tblPr><w:tblGrid>{"".join(f"<w:gridCol w:w=\"{width}\"/>" for _ in range(cols))}</w:tblGrid>']
        for row in t.rows:
            out.append('<w:tr>')
            for c in row:
                tcpr = f'<w:tcW w:w="{width * c.colspan}" w:type="dxa"/>'
                if c.colspan > 1:
                    tcpr += f'<w:gridSpan w:val="{c.colspan}"/>'
                inner = ''.join(self.block(b) for b in c.blocks)
                if not inner.rstrip().endswith('</w:p>'):
                    inner += '<w:p/>'     # a cell must end with a paragraph
                out.append(f'<w:tc><w:tcPr>{tcpr}</w:tcPr>{inner}</w:tc>')
            out.append('</w:tr>')
        out.append('</w:tbl>')
        return ''.join(out)


_BAD_XML = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]')


def _xml_safe(text):
    return _BAD_XML.sub('', text)


def write_docx(doc: Document) -> bytes:
    return _DocxWriter().write(doc)


def make_zip(files, stored_first=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, data in files:
            info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_STORED if name == stored_first else zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data.encode('utf-8') if isinstance(data, str) else data)
    return buf.getvalue()


def image_size(data):
    """Pixel size of a PNG/GIF/JPEG/BMP, or (0, 0)."""
    try:
        if data.startswith(b'\x89PNG'):
            return int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')
        if data[:6] in (b'GIF87a', b'GIF89a'):
            return int.from_bytes(data[6:8], 'little'), int.from_bytes(data[8:10], 'little')
        if data.startswith(b'BM'):
            return int.from_bytes(data[18:22], 'little', signed=True), abs(int.from_bytes(data[22:26], 'little', signed=True))
        if data.startswith(b'\xff\xd8'):
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    return int.from_bytes(data[i + 7:i + 9], 'big'), int.from_bytes(data[i + 5:i + 7], 'big')
                i += 2 + int.from_bytes(data[i + 2:i + 4], 'big')
    except (IndexError, ValueError):
        pass
    return 0, 0


# ---------------------------------------------------------------- pictures without their metadata

# A photo carries where and when it was taken, the camera, sometimes a
# thumbnail of the uncropped picture. Pictures are written into documents
# without any of that. The pixel data is copied as it is, never re-encoded.
#
# What stays is what the picture needs to look the same: its colour profile
# (ICC), JFIF and Adobe colour markers, and the one EXIF field that says which
# way up it is (Orientation), on its own in a fresh EXIF block; programs
# disagree about whether to turn a picture by it, and with the field kept
# each goes on showing the picture the way it did before.

def strip_image_metadata(data: bytes, mime: str = '') -> bytes:
    """The same picture without EXIF, XMP, IPTC, comments, text or dates.
    A file that can't be followed to its end is returned as it was."""
    try:
        if data.startswith(b'\xff\xd8'):
            return _strip_jpeg(data)
        if data.startswith(b'\x89PNG\r\n\x1a\n'):
            return _strip_png(data)
        if data[:6] in (b'GIF87a', b'GIF89a'):
            return _strip_gif(data)
    except (IndexError, ValueError):
        pass
    return data


def _exif_orientation(tiff: bytes) -> int:
    """The Orientation (1-8) in an EXIF block's TIFF data; 1, upright, if there's none."""
    order = {b'II': 'little', b'MM': 'big'}.get(bytes(tiff[:2]))
    if not order or int.from_bytes(tiff[2:4], order) != 42:
        return 1
    ifd = int.from_bytes(tiff[4:8], order)
    count = int.from_bytes(tiff[ifd:ifd + 2], order) if ifd + 2 <= len(tiff) else 0
    for n in range(min(count, 512)):
        entry = tiff[ifd + 2 + 12 * n:ifd + 14 + 12 * n]
        if len(entry) == 12 and int.from_bytes(entry[:2], order) == 0x0112:
            value = int.from_bytes(entry[8:10], order)
            if int.from_bytes(entry[2:4], order) == 3 and int.from_bytes(entry[4:8], order) == 1 and 1 <= value <= 8:
                return value
            break
    return 1


def _orientation_exif(orientation: int) -> bytes:
    """EXIF (TIFF) data with the Orientation field and nothing else."""
    return (b'MM\x00\x2a\x00\x00\x00\x08' + b'\x00\x01' +
            b'\x01\x12\x00\x03\x00\x00\x00\x01' + bytes([0, orientation, 0, 0]) + b'\x00\x00\x00\x00')


def _strip_jpeg(data: bytes) -> bytes:
    out = [b'\xff\xd8']
    kept = []           # segments in front of the picture data, in their order
    orientation = 1
    i, n = 2, len(data)
    while True:
        if i + 1 >= n or data[i] != 0xFF:
            raise ValueError('not a JPEG marker')
        marker = data[i + 1]
        if marker == 0xFF:                      # padding
            i += 1
            continue
        if marker == 0xD9:                      # end of image; anything after it is dropped
            kept.append(b'\xff\xd9')
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            kept.append(data[i:i + 2])
            i += 2
            continue
        size = int.from_bytes(data[i + 2:i + 4], 'big')
        if size < 2 or i + 2 + size > n:
            raise ValueError('JPEG segment runs past the end')
        body = data[i + 4:i + 2 + size]
        end = i + 2 + size
        if marker == 0xDA:                      # a scan: its compressed data follows, up to the next marker
            while True:
                end = data.find(b'\xff', end)
                if end < 0 or end + 1 >= n:     # cut short, as files sometimes are: all of it is picture data
                    end = n
                    break
                if data[end + 1] == 0x00 or 0xD0 <= data[end + 1] <= 0xD7:
                    end += 2
                elif data[end + 1] == 0xFF:
                    end += 1
                else:
                    break
            kept.append(data[i:end])
            if end == n:
                break
        elif marker == 0xE1 and body.startswith(b'Exif\x00\x00'):
            orientation = _exif_orientation(body[6:])
        elif marker == 0xFE or 0xE0 <= marker <= 0xEF:
            # Comments and application data go, except what decides the colours.
            if ((marker == 0xE0 and body.startswith(b'JFIF\x00')) or
                    (marker == 0xE2 and body.startswith(b'ICC_PROFILE\x00')) or
                    (marker == 0xEE and body.startswith(b'Adobe'))):
                kept.append(data[i:end])
        else:
            kept.append(data[i:end])
        i = end
    if orientation != 1:
        exif = b'Exif\x00\x00' + _orientation_exif(orientation)
        block = b'\xff\xe1' + (len(exif) + 2).to_bytes(2, 'big') + exif
        # EXIF goes first, or right after the JFIF header when there is one.
        at = 1 if kept and kept[0][:2] == b'\xff\xe0' else 0
        kept.insert(at, block)
    return b''.join(out + kept)


# PNG chunks that make the picture and its colours; every other chunk (text,
# dates, EXIF, anything a program added for itself) is left out.
_PNG_KEEP = {b'IHDR', b'PLTE', b'IDAT', b'IEND', b'tRNS', b'gAMA', b'cHRM', b'sRGB', b'iCCP', b'sBIT', b'bKGD',
             b'pHYs', b'hIST', b'sPLT', b'cICP', b'mDCv', b'cLLi', b'acTL', b'fcTL', b'fdAT'}


def _strip_png(data: bytes) -> bytes:
    def chunk(kind, body):
        return len(body).to_bytes(4, 'big') + kind + body + (zlib.crc32(kind + body) & 0xffffffff).to_bytes(4, 'big')
    chunks = []
    orientation = 1
    i, n = 8, len(data)
    while True:
        if i + 12 > n:
            raise ValueError('PNG ends in the middle of a chunk')
        size = int.from_bytes(data[i:i + 4], 'big')
        kind = data[i + 4:i + 8]
        end = i + 12 + size
        if end > n:
            raise ValueError('PNG chunk runs past the end')
        if kind == b'eXIf':
            orientation = _exif_orientation(data[i + 8:i + 8 + size])
        elif kind in _PNG_KEEP:
            chunks.append((kind, data[i:end]))
        i = end
        if kind == b'IEND':             # anything after it is dropped
            break
    out = [data[:8]]
    for kind, raw in chunks:
        if kind == b'IDAT' and orientation != 1:      # goes in front of the picture data
            out.append(chunk(b'eXIf', _orientation_exif(orientation)))
            orientation = 1
        out.append(raw)
    return b''.join(out)


def _strip_gif(data: bytes) -> bytes:
    def table(flags):
        return 3 << ((flags & 7) + 1) if flags & 0x80 else 0

    def blocks(at):
        """The end of a run of data sub-blocks that starts at `at`."""
        while data[at]:
            at += 1 + data[at]
        return at + 1
    i = 13 + table(data[10])
    out = [data[:i]]
    while True:
        kind = data[i]
        if kind == 0x3B:                        # the end; anything after it is dropped
            out.append(b';')
            break
        if kind == 0x2C:                        # a picture
            end = blocks(i + 10 + table(data[i + 9]) + 1)
            out.append(data[i:end])
        elif kind == 0x21:
            label = data[i + 1]
            end = blocks(i + 2)
            # Comments go, and so do applications' own blocks (XMP is one),
            # other than the ones that make an animation repeat and the colour profile.
            if label in (0xF9, 0x01) or (label == 0xFF and data[i + 3:i + 14] in (
                    b'NETSCAPE2.0', b'ANIMEXTS1.0', b'ICCRGBG1012')):
                out.append(data[i:end])
        else:
            raise ValueError('not a GIF block')
        if end > len(data):
            raise ValueError('GIF block runs past the end')
        i = end
    return b''.join(out)


# ---------------------------------------------------------------- ODT in

NS = {
    'office': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
    'style': 'urn:oasis:names:tc:opendocument:xmlns:style:1.0',
    'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
    'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
    'draw': 'urn:oasis:names:tc:opendocument:xmlns:drawing:1.0',
    'fo': 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0',
    'xlink': 'http://www.w3.org/1999/xlink',
    'svg': 'urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0',
    'meta': 'urn:oasis:names:tc:opendocument:xmlns:meta:1.0',
    'manifest': 'urn:oasis:names:tc:opendocument:xmlns:manifest:1.0',
    'loext': 'urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0',
}


def q(name):
    prefix, local = name.split(':')
    return f'{{{NS[prefix]}}}{local}'


class _OdtStyles:
    def __init__(self, *roots):
        self.styles = {}        # (family, name) -> element
        self.lists = {}         # name -> element
        for root in roots:
            if root is None:
                continue
            for container in ('office:styles', 'office:automatic-styles'):
                for c in root.iter(q(container)):
                    for st in c.findall(q('style:style')):
                        self.styles[(st.get(q('style:family')), st.get(q('style:name')))] = st
                    for ls in c.findall(q('text:list-style')):
                        self.lists[ls.get(q('style:name'))] = ls

    def chain(self, family, name):
        seen = set()
        while name and (family, name) in self.styles and name not in seen:
            seen.add(name)
            st = self.styles[(family, name)]
            yield st
            name = st.get(q('style:parent-style-name'))

    def text_props(self, family, name):
        props = {}
        for st in self.chain(family, name):
            tp = st.find(q('style:text-properties'))
            if tp is None:
                continue
            fw = tp.get(q('fo:font-weight'))
            if fw is not None:
                props.setdefault('b', fw in ('bold', '600', '700', '800', '900'))
            fs = tp.get(q('fo:font-style'))
            if fs is not None:
                props.setdefault('i', fs in ('italic', 'oblique'))
            us = tp.get(q('style:text-underline-style'))
            if us is not None:
                props.setdefault('u', us != 'none')
            ls = tp.get(q('style:text-line-through-style'))
            if ls is not None:
                props.setdefault('s', ls != 'none')
            pos = tp.get(q('style:text-position'))
            if pos is not None:
                first = (pos.split() or ['0'])[0]
                try:
                    shift = {'super': 33.0, 'sub': -33.0}.get(first) or float(first.rstrip('%'))
                except ValueError:
                    shift = 0.0
                props.setdefault('sup', shift > 0)
                props.setdefault('sub', shift < 0)
        return props

    def para_props(self, name):
        align = ''
        brk = False
        heading = 0
        for st in self.chain('paragraph', name):
            pp = st.find(q('style:paragraph-properties'))
            if pp is not None:
                if not align and pp.get(q('fo:text-align')):
                    align = {'center': 'center', 'end': 'right', 'right': 'right',
                             'justify': 'justify'}.get(pp.get(q('fo:text-align')), '-')
                if pp.get(q('fo:break-before')) == 'page':
                    brk = True
            sname = st.get(q('style:name')) or ''
            m = re.fullmatch(r'Heading_20_([1-6])', sname)
            if not heading and m:
                heading = int(m.group(1))
            elif not heading and sname == 'Title':
                heading = 1
            elif not heading and st.get(q('style:default-outline-level'), '').isdigit() and sname.startswith('Heading'):
                heading = min(6, int(st.get(q('style:default-outline-level'))))
        return (align if align != '-' else ''), brk, heading

    def list_ordered(self, name, level):
        ls = self.lists.get(name)
        if ls is None:
            return False
        for child in ls:
            if child.get(q('text:level')) == str(level + 1):
                return child.tag == q('text:list-level-style-number') and bool(child.get(q('style:num-format'), '1'))
        return False


class _OdtReader:
    def __init__(self, data):
        self.z = open_zip(data)
        self.content = parse_xml(read_member(self.z, 'content.xml'))
        styles_data = read_member(self.z, 'styles.xml', False)
        self.styles = _OdtStyles(parse_xml(styles_data) if styles_data else None, self.content)
        self.warnings = set()

    def read(self):
        text = self.content.find(f"{q('office:body')}/{q('office:text')}")
        if text is None:
            raise FormatError('this OpenDocument file isn\'t a text document')
        if self.content.find(f".//{q('text:tracked-changes')}") is not None:
            self.warnings.add('tracked changes (shown as accepted)')
        doc = Document(self.blocks(text, None, 0))
        if self.styles_have_header():
            self.warnings.add('headers and footers')
        doc.warnings = sorted(self.warnings)
        return doc

    def styles_have_header(self):
        data = read_member(self.z, 'styles.xml', False) or b''
        return b'<style:header' in data or b'<style:footer' in data

    def blocks(self, parent, list_style, level):
        out = []
        for el in parent:
            tag = el.tag
            if tag in (q('text:p'), q('text:h')):
                style = el.get(q('text:style-name'))
                align, brk, heading = self.styles.para_props(style)
                if tag == q('text:h'):
                    heading = int(el.get(q('text:outline-level'), '1') or 1)
                if brk and out:
                    out.append(PageBreak())
                base = {} if heading else self.styles.text_props('paragraph', style)
                runs, floats = self.inline(el, base, None)
                p = Para(merge_runs(runs), f'h{min(max(heading, 1), 6)}' if heading else 'p', align)
                out.append(p)
            elif tag == q('text:list'):
                name = el.get(q('text:style-name')) or list_style
                lst = ListBlock(self.styles.list_ordered(name, level))
                for item in el:
                    if item.tag in (q('text:list-item'), q('text:list-header')):
                        lst.items.append(self.blocks(item, name, level + 1) or [Para()])
                out.append(lst)
            elif tag == q('table:table'):
                out.append(self.table(el))
            elif tag in (q('text:section'), q('text:index-body'), q('text:table-of-content'),
                         q('text:illustration-index'), q('text:alphabetical-index'), q('text:bibliography'),
                         q('text:user-index'), q('text:object-index'), q('text:table-index')):
                out.extend(self.blocks(el, list_style, level))
            elif tag == q('text:soft-page-break'):
                pass
        return out

    def inline(self, parent, fmt, href):
        runs = []
        if parent.text:
            runs.append(self.text(parent.text, fmt, href))
        for el in parent:
            tag = el.tag
            if tag == q('text:span'):
                props = dict(fmt)
                props.update(self.styles.text_props('text', el.get(q('text:style-name'))))
                runs.extend(self.inline(el, props, href)[0])
            elif tag == q('text:a'):
                link = el.get(q('xlink:href'))
                if link and not re.match(r'(?i)(https?|mailto):', link):
                    link = None
                runs.extend(self.inline(el, fmt, link)[0])
            elif tag == q('text:s'):
                runs.append(self.text(' ' * min(int(el.get(q('text:c'), '1') or 1), 1000), fmt, href, True))
            elif tag == q('text:tab'):
                runs.append(self.text('\t', fmt, href, True))
            elif tag == q('text:line-break'):
                runs.append(Break())
            elif tag == q('draw:frame'):
                runs.extend(self.frame(el))
            elif tag == q('draw:a'):
                for fr in el.findall(q('draw:frame')):
                    runs.extend(self.frame(fr))
            elif tag in (q('text:note'),):
                self.warnings.add('footnotes and endnotes')
            elif tag == q('office:annotation'):
                self.warnings.add('comments')
            elif tag in (q('office:annotation-end'), q('text:bookmark'), q('text:bookmark-start'),
                         q('text:bookmark-end'), q('text:soft-page-break'), q('text:reference-mark-start'),
                         q('text:reference-mark-end'), q('text:change-start'), q('text:change-end'), q('text:change')):
                pass
            elif tag.startswith('{' + NS['draw'] + '}'):
                self.warnings.add('text boxes and shapes')
            else:
                # fields (page numbers, dates...), meta, ruby...: keep their text
                runs.extend(self.inline(el, fmt, href)[0])
            if el.tail:
                runs.append(self.text(el.tail, fmt, href))
        return runs, None

    @staticmethod
    def text(t, fmt, href, literal=False):
        if not literal:     # whitespace in ODF text collapses like in HTML
            t = re.sub(r'[ \t\r\n]+', ' ', t)
        u = fmt.get('u', False) and not href
        return Text(t, fmt.get('b', False), fmt.get('i', False), u, fmt.get('s', False),
                    fmt.get('sup', False), fmt.get('sub', False), href)

    def frame(self, fr):
        img = fr.find(q('draw:image'))
        if img is None:
            if fr.find(q('draw:text-box')) is not None:
                self.warnings.add('text boxes and shapes')
            elif fr.find(q('draw:object')) is not None or fr.find(q('draw:object-ole')) is not None:
                self.warnings.add('embedded objects (charts, formulas)')
            return []
        target = img.get(q('xlink:href')) or ''
        if re.match(r'(?i)[a-z][a-z0-9+.-]*:', target):
            self.warnings.add('pictures linked from the internet (not loaded)')
            return []
        data = read_member(self.z, target.lstrip('./'), False)
        if not data or len(data) > MAX_IMAGE:
            return []
        mime = sniff_image(data)
        if not mime:
            self.warnings.add('pictures in formats Leaflet can\'t show (EMF/WMF/SVG)')
            return []
        desc = fr.find(q('svg:desc'))
        title = fr.find(q('svg:title'))
        alt = (desc.text if desc is not None else None) or (title.text if title is not None else None) or ''
        return [Image(data, mime, css_length_px(fr.get(q('svg:width'), '')),
                      css_length_px(fr.get(q('svg:height'), '')), alt)]

    def table(self, el):
        t = Table()
        for row in self.rows(el):
            cells = []
            for c in row:
                if c.tag == q('table:table-cell'):
                    span = int(c.get(q('table:number-columns-spanned'), '1') or 1)
                    if int(c.get(q('table:number-rows-spanned'), '1') or 1) > 1:
                        self.warnings.add('table cells merged across rows (split again)')
                    cells.append(Cell(self.blocks(c, None, 0) or [Para()], max(1, min(span, 64))))
                elif c.tag == q('table:covered-table-cell'):
                    pass
            if cells:
                t.rows.append(cells)
        return t

    def rows(self, el):
        for child in el:
            if child.tag == q('table:table-row'):
                yield child
            elif child.tag in (q('table:table-header-rows'), q('table:table-rows'), q('table:table-row-group')):
                yield from self.rows(child)


def read_odt(data: bytes) -> Document:
    return _OdtReader(data).read()


# ---------------------------------------------------------------- ODT out

ODT_NS = ' '.join(f'xmlns:{k}="{v}"' for k, v in NS.items() if k not in ('manifest', 'meta', 'loext'))


def _odt_styles_xml():
    heads = ''.join(
        f'<style:style style:name="Heading_20_{n}" style:display-name="Heading {n}" style:family="paragraph" '
        f'style:parent-style-name="Heading" style:next-style-name="Text_20_body" style:default-outline-level="{n}" '
        f'style:class="text"><style:text-properties fo:font-size="{_HEADING_PT[n]}pt" fo:font-weight="bold" '
        f'style:font-weight-asian="bold" style:font-weight-complex="bold"/></style:style>' for n in range(1, 7))
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<office:document-styles {ODT_NS} office:version="1.3">
<office:font-face-decls><style:font-face style:name="Liberation Sans" svg:font-family="&apos;Liberation Sans&apos;" style:font-family-generic="swiss"/></office:font-face-decls>
<office:styles>
<style:default-style style:family="paragraph"><style:paragraph-properties fo:margin-top="0cm" fo:margin-bottom="0.21cm"/>
<style:text-properties style:font-name="Liberation Sans" fo:font-size="11pt" fo:language="en" fo:country="US"/></style:default-style>
<style:style style:name="Standard" style:family="paragraph" style:class="text"/>
<style:style style:name="Text_20_body" style:display-name="Text body" style:family="paragraph" style:parent-style-name="Standard" style:class="text"/>
<style:style style:name="Heading" style:family="paragraph" style:parent-style-name="Standard" style:next-style-name="Text_20_body" style:class="text">
<style:paragraph-properties fo:margin-top="0.42cm" fo:margin-bottom="0.21cm" fo:keep-with-next="always"/></style:style>
{heads}
<style:style style:name="Table_20_Contents" style:display-name="Table Contents" style:family="paragraph" style:parent-style-name="Standard" style:class="extra">
<style:paragraph-properties fo:margin-bottom="0cm"/></style:style>
<style:style style:name="Internet_20_link" style:display-name="Internet link" style:family="text">
<style:text-properties fo:color="#000080" style:text-underline-style="solid" style:text-underline-width="auto" style:text-underline-color="font-color"/></style:style>
</office:styles>
<office:automatic-styles><style:page-layout style:name="pm1"><style:page-layout-properties fo:page-width="21.001cm" fo:page-height="29.7cm"
 style:print-orientation="portrait" fo:margin-top="2cm" fo:margin-bottom="2cm" fo:margin-left="2.5cm" fo:margin-right="2.5cm"/></style:page-layout></office:automatic-styles>
<office:master-styles><style:master-page style:name="Standard" style:page-layout-name="pm1"/></office:master-styles>
</office:document-styles>'''


class _OdtWriter:
    def __init__(self):
        self.text_styles = {}       # (b,i,u,s,sup,sub) -> name
        self.para_styles = {}       # (parent, align, break) -> name
        self.pictures = []          # (path, mime, data)
        self.tables = 0
        self.lists = {}             # ordered -> name

    def text_style(self, r):
        key = (r.b, r.i, r.u, r.s, r.sup, r.sub)
        if not any(key):
            return None
        if key not in self.text_styles:
            self.text_styles[key] = f'T{len(self.text_styles) + 1}'
        return self.text_styles[key]

    def para_style(self, parent, align, brk=False):
        if not align and not brk:
            return parent
        key = (parent, align, brk)
        if key not in self.para_styles:
            self.para_styles[key] = f'P{len(self.para_styles) + 1}'
        return self.para_styles[key]

    def list_style(self, ordered):
        if ordered not in self.lists:
            self.lists[ordered] = 'LNum' if ordered else 'LBul'
        return self.lists[ordered]

    def write(self, doc):
        self.pending_break = False
        body = ''.join(self.block(b) for b in doc.blocks) or '<text:p text:style-name="Standard"/>'
        if self.pending_break:
            body += f'<text:p text:style-name="{self.para_style("Standard", "", True)}"/>'
        auto = []
        for (b, i, u, s, sup, sub), name in self.text_styles.items():
            props = ''
            if b:
                props += ' fo:font-weight="bold" style:font-weight-asian="bold" style:font-weight-complex="bold"'
            if i:
                props += ' fo:font-style="italic" style:font-style-asian="italic" style:font-style-complex="italic"'
            if u:
                props += ' style:text-underline-style="solid" style:text-underline-width="auto" style:text-underline-color="font-color"'
            if s:
                props += ' style:text-line-through-style="solid" style:text-line-through-type="single"'
            if sup or sub:
                props += f' style:text-position="{"super" if sup else "sub"} 58%"'
            auto.append(f'<style:style style:name="{name}" style:family="text"><style:text-properties{props}/></style:style>')
        for (parent, align, brk), name in self.para_styles.items():
            props = ''
            if align:
                props += f' fo:text-align="{ {"right": "end", "center": "center", "justify": "justify"}[align] }"'
            if brk:
                props += ' fo:break-before="page"'
            auto.append(f'<style:style style:name="{name}" style:family="paragraph" style:parent-style-name="{parent}">'
                        f'<style:paragraph-properties{props}/></style:style>')
        for n in range(1, self.tables + 1):
            auto.append(f'<style:style style:name="Table{n}" style:family="table"><style:table-properties style:width="16cm" table:align="margins"/></style:style>')
        if self.tables:
            auto.append('<style:style style:name="TableCell" style:family="table-cell"><style:table-cell-properties '
                        'fo:padding="0.1cm" fo:border="0.5pt solid #000000"/></style:style>')
        auto.append('<style:style style:name="fr1" style:family="graphic"><style:graphic-properties '
                    'style:vertical-pos="top" style:vertical-rel="baseline" style:horizontal-pos="center" style:horizontal-rel="paragraph"/></style:style>')
        for ordered, name in self.lists.items():
            levels = []
            for n in range(1, 11):
                indent = f'<style:list-level-properties text:list-level-position-and-space-mode="label-alignment">' \
                         f'<style:list-level-label-alignment text:label-followed-by="listtab" text:list-tab-stop-position="{0.635 * n:.3f}cm" ' \
                         f'fo:text-indent="-0.635cm" fo:margin-left="{0.635 * n:.3f}cm"/></style:list-level-properties>'
                if ordered:
                    fmt = ['1', 'a', 'i'][(n - 1) % 3]
                    levels.append(f'<text:list-level-style-number text:level="{n}" style:num-suffix="." style:num-format="{fmt}">{indent}</text:list-level-style-number>')
                else:
                    ch = ['\u2022', '\u25e6', '\u25aa'][(n - 1) % 3]
                    levels.append(f'<text:list-level-style-bullet text:level="{n}" text:bullet-char="{ch}">{indent}</text:list-level-style-bullet>')
            auto.append(f'<text:list-style style:name="{name}">{"".join(levels)}</text:list-style>')
        content = (f'<?xml version="1.0" encoding="UTF-8"?>\n<office:document-content {ODT_NS} office:version="1.3">'
                   f'<office:automatic-styles>{"".join(auto)}</office:automatic-styles>'
                   f'<office:body><office:text>{body}</office:text></office:body></office:document-content>')
        manifest = ['<?xml version="1.0" encoding="UTF-8"?>\n'
                    f'<manifest:manifest xmlns:manifest="{NS["manifest"]}" manifest:version="1.3">'
                    '<manifest:file-entry manifest:full-path="/" manifest:version="1.3" manifest:media-type="application/vnd.oasis.opendocument.text"/>'
                    '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
                    '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>']
        for path, mime, _ in self.pictures:
            manifest.append(f'<manifest:file-entry manifest:full-path="{path}" manifest:media-type="{mime}"/>')
        manifest.append('</manifest:manifest>')
        files = [('mimetype', 'application/vnd.oasis.opendocument.text'),
                 ('content.xml', content), ('styles.xml', _odt_styles_xml()),
                 ('META-INF/manifest.xml', ''.join(manifest))]
        files += [(path, data) for path, _, data in self.pictures]
        return make_zip(files, stored_first='mimetype')

    def take_break(self):
        brk = self.pending_break
        self.pending_break = False
        return brk

    def block(self, b, in_cell=False):
        if isinstance(b, PageBreak):
            self.pending_break = True
            return ''
        if isinstance(b, Para):
            return self.para(b, in_cell)
        if isinstance(b, ListBlock):
            return self.list(b, True)
        if isinstance(b, Table):
            return self.table(b)
        return ''

    def para(self, p, in_cell=False, parent=None):
        brk = self.take_break()
        if p.style.startswith('h') and p.style[1:].isdigit():
            n = p.style[1:]
            style = self.para_style(f'Heading_20_{n}', p.align, brk)
            return f'<text:h text:style-name="{style}" text:outline-level="{n}">{self.runs(p.runs)}</text:h>'
        style = self.para_style(parent or ('Table_20_Contents' if in_cell else 'Standard'), p.align, brk)
        return f'<text:p text:style-name="{style}">{self.runs(p.runs)}</text:p>'

    def list(self, lst, top):
        name = self.list_style(lst.ordered)
        out = [f'<text:list text:style-name="{name}">']
        for item in lst.items:
            out.append('<text:list-item>')
            inner = ''.join(self.list(b, False) if isinstance(b, ListBlock) else self.block(b) for b in item)
            out.append(inner or '<text:p text:style-name="Standard"/>')
            out.append('</text:list-item>')
        out.append('</text:list>')
        return ''.join(out)

    def table(self, t):
        self.tables += 1
        cols = max((sum(c.colspan for c in row) for row in t.rows), default=1)
        out = [f'<table:table table:name="Table{self.tables}" table:style-name="Table{self.tables}">'
               f'<table:table-column table:number-columns-repeated="{cols}"/>']
        for row in t.rows:
            out.append('<table:table-row>')
            used = 0
            for c in row:
                span = f' table:number-columns-spanned="{c.colspan}"' if c.colspan > 1 else ''
                inner = ''.join(self.block(b, True) for b in c.blocks) or '<text:p text:style-name="Table_20_Contents"/>'
                out.append(f'<table:table-cell table:style-name="TableCell" office:value-type="string"{span}>{inner}</table:table-cell>')
                out.extend('<table:covered-table-cell/>' for _ in range(c.colspan - 1))
                used += c.colspan
            out.extend('<table:table-cell table:style-name="TableCell" office:value-type="string"><text:p/></table:table-cell>'
                       for _ in range(cols - used))
            out.append('</table:table-row>')
        out.append('</table:table>')
        return ''.join(out)

    def runs(self, runs):
        out = []
        for r in runs:
            if isinstance(r, Break):
                out.append('<text:line-break/>')
            elif isinstance(r, Image):
                out.append(self.image(r))
            elif isinstance(r, Text):
                t = self.text(r.text)
                style = self.text_style(r)
                if style:
                    t = f'<text:span text:style-name="{style}">{t}</text:span>'
                if r.href:
                    t = f'<text:a xlink:type="simple" xlink:href="{_x(r.href)}" text:style-name="Internet_20_link">{t}</text:a>'
                out.append(t)
        return ''.join(out)

    @staticmethod
    def text(t):
        t = _xml_safe(t)
        out = []
        for part in re.split(r'(\t| {2,})', t):
            if part == '\t':
                out.append('<text:tab/>')
            elif part.startswith('  '):
                out.append(f' <text:s text:c="{len(part) - 1}"/>')
            else:
                out.append(_x(part))
        res = ''.join(out)
        if res.startswith(' '):
            res = '<text:s/>' + res[1:]
        return res

    def image(self, im):
        ext = IMAGE_TYPES.get(im.mime)
        if not ext:
            return ''
        n = len(self.pictures) + 1
        path = f'Pictures/image{n}.{ext}'
        self.pictures.append((path, im.mime, strip_image_metadata(im.data, im.mime)))
        wpx, hpx = im.width or image_size(im.data)[0] or 200, im.height or image_size(im.data)[1] or 150
        if wpx > 604:
            hpx = int(hpx * 604 / wpx)
            wpx = 604
        alt = f'<svg:desc>{_x(im.alt)}</svg:desc>' if im.alt else ''
        return (f'<draw:frame draw:style-name="fr1" draw:name="Image{n}" text:anchor-type="as-char" '
                f'svg:width="{wpx / 96 * 2.54:.3f}cm" svg:height="{hpx / 96 * 2.54:.3f}cm" draw:z-index="0">'
                f'<draw:image xlink:href="{path}" xlink:type="simple" xlink:show="embed" xlink:actuate="onLoad"/>{alt}</draw:frame>')


def write_odt(doc: Document) -> bytes:
    return _OdtWriter().write(doc)


# ---------------------------------------------------------------- RTF and text in

_RTF_SKIP = {'fonttbl', 'colortbl', 'stylesheet', 'info', 'pict', 'header', 'footer', 'headerl', 'headerr',
             'headerf', 'footerl', 'footerr', 'footerf', 'footnote', 'annotation', 'object', 'listtable',
             'listoverridetable', 'rsidtbl', 'generator', 'xmlnstbl', 'themedata', 'colorschememapping',
             'latentstyles', 'datastore', 'filetbl', 'revtbl', 'pgdsctbl', 'mmathPr', 'fldinst', 'bkmkstart',
             'bkmkend', 'shp', 'shpinst', 'nonshppict', 'blipuid', 'userprops', 'docvar', 'ftnsep', 'ftnsepc',
             'aftnsep', 'aftnsepc', 'pn', 'pntext', 'listtext', 'wgrffmtfilter', 'xe', 'tc', 'field'}
_RTF_TOKEN = re.compile(rb"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|[\r\n]+|([^\\{}\r\n]+)", re.S)


def read_rtf(data: bytes) -> Document:
    warnings = set()
    blocks = []
    runs = []
    para = Para()
    state = {'b': False, 'i': False, 'u': False, 's': False, 'sup': False, 'sub': False, 'skip': False, 'uc': 1}
    stack = []
    codepage = 'cp1252'
    skip_chars = 0
    pending_bytes = bytearray()
    in_table = False
    row_cells = []
    rows = []

    def flush_bytes():
        if pending_bytes:
            try:
                add(pending_bytes.decode(codepage, errors='replace'))
            except LookupError:
                add(pending_bytes.decode('cp1252', errors='replace'))
            pending_bytes.clear()

    def add(text):
        if state['skip'] or not text:
            return
        runs.append(Text(text, state['b'], state['i'], state['u'], state['s'], state['sup'], state['sub']))

    def end_para():
        nonlocal runs, para
        flush_bytes()
        para.runs = merge_runs(runs)
        if in_table:
            row_cells.append(para)
        else:
            blocks.append(para)
        runs = []
        para = Para(align=para.align)

    group_start = True
    for m in _RTF_TOKEN.finditer(data):
        word, num, hexbyte, sym, brace, text = m.groups()
        if hexbyte is not None:
            if skip_chars:
                skip_chars -= 1
                continue
            pending_bytes.append(int(hexbyte, 16))
            continue
        flush_bytes()
        if brace == b'{':
            stack.append(dict(state))
            group_start = True
            continue
        if brace == b'}':
            if stack:
                state = stack.pop()
            group_start = False
            continue
        if sym is not None:
            if sym == b'*':
                state['skip'] = True
            elif sym in (b'\\', b'{', b'}'):
                add(sym.decode())
            elif sym == b'~':
                add('\u00a0')
            elif sym == b'_':
                add('\u2011')
            elif sym in (b'\n', b'\r'):
                end_para()
            group_start = False
            continue
        if word is not None:
            w_ = word.decode()
            n = int(num) if num else None
            if group_start and w_ in _RTF_SKIP:
                state['skip'] = True
                if w_ == 'pict':
                    warnings.add('pictures in RTF files (not loaded)')
                elif w_ in ('header', 'footer', 'headerl', 'headerr', 'footerl', 'footerr'):
                    warnings.add('headers and footers')
                elif w_ == 'footnote':
                    warnings.add('footnotes and endnotes')
            group_start = False
            if w_ == 'ansicpg' and n:
                codepage = f'cp{n}'
            elif w_ in ('b', 'i', 'strike'):
                state['s' if w_ == 'strike' else w_] = n != 0
            elif w_ == 'ul':
                state['u'] = n != 0
            elif w_ == 'ulnone':
                state['u'] = False
            elif w_ == 'super':
                state['sup'], state['sub'] = True, False
            elif w_ == 'sub':
                state['sup'], state['sub'] = False, True
            elif w_ == 'nosupersub':
                state['sup'] = state['sub'] = False
            elif w_ == 'plain':
                for k in ('b', 'i', 'u', 's', 'sup', 'sub'):
                    state[k] = False
            elif w_ in ('par', 'sect'):
                end_para()
            elif w_ == 'page':
                end_para()
                blocks.append(PageBreak())
            elif w_ == 'line':
                if not state['skip']:
                    runs.append(Break())
            elif w_ == 'tab':
                add('\t')
            elif w_ == 'pard':
                para.align = ''
            elif w_ in ('qc', 'qr', 'qj', 'ql'):
                para.align = {'qc': 'center', 'qr': 'right', 'qj': 'justify', 'ql': ''}[w_]
            elif w_ == 'uc' and n is not None:
                state['uc'] = n
            elif w_ == 'u' and n is not None:
                add(chr(n % 65536))
                skip_chars = state['uc']
            elif w_ in ('emdash', 'endash', 'bullet', 'lquote', 'rquote', 'ldblquote', 'rdblquote'):
                add({'emdash': '\u2014', 'endash': '\u2013', 'bullet': '\u2022', 'lquote': '\u2018',
                     'rquote': '\u2019', 'ldblquote': '\u201c', 'rdblquote': '\u201d'}[w_])
            elif w_ == 'trowd':
                in_table = True
            elif w_ == 'cell':
                flush_bytes()
                para.runs = merge_runs(runs)
                row_cells.append(para)
                runs = []
                para = Para()
                row_cells.append(None)      # cell boundary
            elif w_ == 'row':
                cells = []
                cur = []
                for x in row_cells:
                    if x is None:
                        cells.append(Cell(cur or [Para()]))
                        cur = []
                    else:
                        cur.append(x)
                if cells:
                    rows.append(cells)
                row_cells.clear()
                in_table = False
            elif w_ == 'intbl':
                in_table = True
            if w_ not in ('intbl', 'trowd', 'cell', 'row') and in_table is False and rows:
                blocks.append(Table(list(rows)))
                rows.clear()
            continue
        if text is not None:
            group_start = False
            t = text.decode(codepage, errors='replace') if codepage else text.decode('latin-1')
            if skip_chars:
                cut = min(skip_chars, len(t))
                t = t[cut:]
                skip_chars -= cut
            add(t)
    flush_bytes()
    if runs:
        end_para()
    if rows:
        blocks.append(Table(rows))
    if not blocks:
        blocks.append(Para())
    return Document(blocks, sorted(warnings))


def read_txt(data: bytes) -> Document:
    if data.startswith(b'\xef\xbb\xbf'):
        text = data[3:].decode('utf-8', errors='replace')
    elif data.startswith((b'\xff\xfe', b'\xfe\xff')):
        text = data.decode('utf-16', errors='replace')
    else:
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            text = data.decode('cp1252', errors='replace')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    blocks = []
    for line in text.split('\n'):
        if line == '\f':
            blocks.append(PageBreak())
        else:
            blocks.append(Para([Text(line)] if line else []))
    if len(blocks) > 1 and isinstance(blocks[-1], Para) and not blocks[-1].runs:
        blocks.pop()   # the file's last newline
    return Document(blocks or [Para()])


# ---------------------------------------------------------------- model <-> editor HTML

def to_html(doc: Document) -> str:
    return ''.join(_block_html(b) for b in doc.blocks) or '<p><br></p>'


def _block_html(b):
    if isinstance(b, Para):
        tag = b.style if re.fullmatch(r'h[1-6]', b.style) else 'p'
        style = f' style="text-align: {b.align}"' if b.align else ''
        inner = _runs_html(b.runs)
        return f'<{tag}{style}>{inner or "<br>"}</{tag}>'
    if isinstance(b, ListBlock):
        tag = 'ol' if b.ordered else 'ul'
        items = []
        for item in b.items:
            # A list item's first paragraph is written without <p>, like the editor does.
            parts = []
            for k, x in enumerate(item):
                if k == 0 and isinstance(x, Para) and x.style == 'p' and not x.align:
                    parts.append(_runs_html(x.runs) or '<br>')
                else:
                    parts.append(_block_html(x))
            items.append(f'<li>{"".join(parts) or "<br>"}</li>')
        return f'<{tag}>{"".join(items)}</{tag}>'
    if isinstance(b, Table):
        rows = []
        for row in b.rows:
            cells = ''.join(
                f'<td{f" colspan={chr(34)}{c.colspan}{chr(34)}" if c.colspan > 1 else ""}>'
                f'{"".join(_block_html(x) for x in c.blocks) or "<p><br></p>"}</td>' for c in row)
            rows.append(f'<tr>{cells}</tr>')
        return f'<table><tbody>{"".join(rows)}</tbody></table>'
    if isinstance(b, PageBreak):
        return '<hr class="page-break">'
    return ''


def _runs_html(runs):
    out = []
    for r in runs:
        if isinstance(r, Break):
            out.append('<br>')
        elif isinstance(r, Image):
            src = f'data:{r.mime};base64,{base64.b64encode(r.data).decode()}'
            size = (f' width="{r.width}"' if r.width else '') + (f' height="{r.height}"' if r.height else '')
            out.append(f'<img src="{src}"{size} alt="{_x(r.alt)}">')
        elif isinstance(r, Text):
            t = _x(r.text).replace('\t', '<span class="tab">\t</span>')
            # Runs of spaces would collapse in HTML.
            t = re.sub(r'  +', lambda m: ' ' + '\u00a0' * (len(m.group()) - 1), t)
            for flag, tag in ((r.sub, 'sub'), (r.sup, 'sup'), (r.s, 's'), (r.u, 'u'), (r.i, 'i'), (r.b, 'b')):
                if flag:
                    t = f'<{tag}>{t}</{tag}>'
            if r.href:
                t = f'<a href="{_x(r.href)}">{t}</a>'
            out.append(t)
    return ''.join(out)


class _Node:
    __slots__ = ('tag', 'attrs', 'children', 'parent')

    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag
        self.attrs = dict(attrs or ())
        self.children = []
        self.parent = parent


_VOID = {'br', 'img', 'hr', 'input', 'meta', 'link', 'col', 'wbr', 'area', 'base', 'source'}
_DROP = {'script', 'style', 'head', 'title', 'template', 'noscript', 'iframe', 'object', 'embed', 'svg', 'math',
         'button', 'select', 'textarea', 'canvas', 'video', 'audio'}


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node('root')
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self.cur)
        self.cur.children.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        node = _Node(tag, attrs, self.cur)
        self.cur.children.append(node)

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


_BLOCK_TAGS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'table', 'hr', 'blockquote', 'pre',
               'section', 'article', 'header', 'footer', 'li', 'dl', 'dt', 'dd', 'figure', 'figcaption', 'address',
               'main', 'nav', 'aside'}


def from_html(source: str) -> Document:
    """The editor's HTML back into the model. Unknown tags keep their text."""
    tb = _TreeBuilder()
    tb.feed(source)
    tb.close()
    return Document(_html_blocks(tb.root, {}))


def _style_of(node):
    out = {}
    for decl in (node.attrs.get('style') or '').split(';'):
        if ':' in decl:
            k, v = decl.split(':', 1)
            out[k.strip().lower()] = v.strip().lower()
    return out


def _fmt_of(node, fmt):
    fmt = dict(fmt)
    tag = node.tag
    if tag in ('b', 'strong'):
        fmt['b'] = True
    elif tag in ('i', 'em', 'cite', 'var', 'dfn'):
        fmt['i'] = True
    elif tag in ('u', 'ins'):
        fmt['u'] = True
    elif tag in ('s', 'strike', 'del'):
        fmt['s'] = True
    elif tag == 'sup':
        fmt['sup'], fmt['sub'] = True, False
    elif tag == 'sub':
        fmt['sub'], fmt['sup'] = True, False
    elif tag == 'a':
        href = node.attrs.get('href') or ''
        if re.match(r'(?i)(https?|mailto):', href):
            fmt['href'] = href
    st = _style_of(node)
    if 'font-weight' in st:
        fmt['b'] = st['font-weight'] in ('bold', 'bolder', '600', '700', '800', '900')
    if 'font-style' in st:
        fmt['i'] = st['font-style'] in ('italic', 'oblique')
    deco = st.get('text-decoration') or st.get('text-decoration-line') or ''
    if 'underline' in deco:
        fmt['u'] = True
    if 'line-through' in deco:
        fmt['s'] = True
    if st.get('vertical-align') == 'super':
        fmt['sup'] = True
    elif st.get('vertical-align') == 'sub':
        fmt['sub'] = True
    return fmt


def _image_of(node):
    src = node.attrs.get('src') or ''
    m = re.match(r'data:(image/(?:png|jpeg|jpg|gif|bmp));base64,([A-Za-z0-9+/=\s]+)$', src)
    if not m:
        return None
    try:
        data = base64.b64decode(m.group(2))
    except ValueError:
        return None
    mime = sniff_image(data)
    if not mime or len(data) > MAX_IMAGE:
        return None

    def num(v):
        v = (v or '').strip().removesuffix('px')
        return int(float(v)) if re.fullmatch(r'\d+(\.\d+)?', v) else 0
    st = _style_of(node)
    width = num(node.attrs.get('width')) or num(st.get('width'))
    height = num(node.attrs.get('height')) or num(st.get('height'))
    return Image(data, mime, width, height, node.attrs.get('alt') or '')


def _html_inline(node, fmt, out):
    for child in node.children:
        if isinstance(child, str):
            t = re.sub(r'[ \r\n]+', ' ', child).replace('\u00a0', ' ')
            if t:
                out.append(Text(t, fmt.get('b', False), fmt.get('i', False), fmt.get('u', False),
                                fmt.get('s', False), fmt.get('sup', False), fmt.get('sub', False), fmt.get('href')))
            continue
        if child.tag in _DROP:
            continue
        if child.tag == 'br':
            out.append(Break())
        elif child.tag == 'img':
            im = _image_of(child)
            if im:
                out.append(im)
        else:
            _html_inline(child, _fmt_of(child, fmt), out)


def _clean_runs(runs):
    runs = merge_runs(runs)
    # A lone trailing <br> is how the editor keeps an empty line open.
    if runs and isinstance(runs[-1], Break):
        runs.pop()
    if runs and isinstance(runs[0], Text):
        runs[0] = Text(runs[0].text.lstrip(' '), **{k: getattr(runs[0], k) for k in ('b', 'i', 'u', 's', 'sup', 'sub', 'href')})
    if runs and isinstance(runs[-1], Text):
        r = runs[-1]
        runs[-1] = Text(r.text.rstrip(' '), r.b, r.i, r.u, r.s, r.sup, r.sub, r.href)
    return [r for r in runs if not (isinstance(r, Text) and not r.text)]


def _align_of(node):
    a = _style_of(node).get('text-align', '') or (node.attrs.get('align') or '').lower()
    return {'center': 'center', 'right': 'right', 'end': 'right', 'justify': 'justify'}.get(a, '')


def _html_blocks(node, fmt, align=''):
    blocks = []
    pending = []    # inline content waiting for a paragraph

    def flush():
        if pending:
            runs = _clean_runs(pending)
            if runs:
                blocks.append(Para(runs, 'p', align))
            pending.clear()

    for child in node.children:
        if isinstance(child, str) or child.tag not in _BLOCK_TAGS:
            if isinstance(child, str):
                if child.strip() or pending:
                    _html_inline(_wrap(child), fmt, pending)
            elif child.tag in _DROP:
                continue
            elif child.tag == 'br':
                if pending:
                    pending.append(Break())
                else:
                    blocks.append(Para([], 'p', align))
            elif child.tag == 'img':
                im = _image_of(child)
                if im:
                    pending.append(im)
            elif any(isinstance(c, _Node) and c.tag in _BLOCK_TAGS for c in _descend(child)):
                flush()
                blocks.extend(_html_blocks(child, _fmt_of(child, fmt), align))
            else:
                _html_inline(child, _fmt_of(child, fmt), pending)
            continue
        flush()
        tag = child.tag
        if tag in ('p', 'div', 'pre', 'address', 'dt', 'dd', 'figcaption') or re.fullmatch(r'h[1-6]', tag):
            inner_blocks = any(isinstance(c, _Node) and c.tag in _BLOCK_TAGS for c in child.children)
            a = _align_of(child) or align
            if inner_blocks:
                blocks.extend(_html_blocks(child, _fmt_of(child, fmt), a))
                continue
            runs = []
            _html_inline(child, _fmt_of(child, fmt) if tag[0] != 'h' else fmt, runs)
            runs = _clean_runs(runs)
            style = tag if re.fullmatch(r'h[1-6]', tag) else 'p'
            if tag == 'div' and not runs and not child.children:
                continue
            blocks.append(Para(runs, style, a))
        elif tag in ('ul', 'ol'):
            lst = ListBlock(tag == 'ol')
            for li in child.children:
                if isinstance(li, _Node) and li.tag == 'li':
                    item = _html_blocks(li, fmt)
                    lst.items.append(item or [Para()])
                elif isinstance(li, _Node) and li.tag in ('ul', 'ol'):
                    # a list directly inside a list belongs to the previous item
                    sub = _html_blocks(_holder(li), fmt)
                    if lst.items:
                        lst.items[-1].extend(sub)
                    else:
                        lst.items.append([Para()] + sub)
            if lst.items:
                blocks.append(lst)
        elif tag == 'li':
            blocks.extend(_html_blocks(child, fmt, align))
        elif tag == 'table':
            t = Table()
            for tr in _rows(child):
                row = []
                for td in tr.children:
                    if isinstance(td, _Node) and td.tag in ('td', 'th'):
                        try:
                            span = max(1, min(int(td.attrs.get('colspan') or 1), 64))
                        except ValueError:
                            span = 1
                        cfmt = dict(fmt, b=True) if td.tag == 'th' else fmt
                        row.append(Cell(_html_blocks(td, cfmt) or [Para()], span))
                if row:
                    t.rows.append(row)
            if t.rows:
                blocks.append(t)
        elif tag == 'hr':
            if 'page-break' in (child.attrs.get('class') or ''):
                blocks.append(PageBreak())
        else:   # blockquote, section, figure...
            blocks.extend(_html_blocks(child, _fmt_of(child, fmt), _align_of(child) or align))
    flush()
    return blocks


def _wrap(text):
    n = _Node('span')
    n.children.append(text)
    return n


def _holder(child):
    n = _Node('div')
    n.children.append(child)
    return n


def _descend(node):
    for c in node.children:
        if isinstance(c, _Node):
            yield c
            yield from _descend(c)


def _rows(table):
    for c in table.children:
        if isinstance(c, _Node):
            if c.tag == 'tr':
                yield c
            elif c.tag in ('tbody', 'thead', 'tfoot'):
                yield from _rows(c)
