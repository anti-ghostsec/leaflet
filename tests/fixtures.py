"""Test files for Leaflet, made from scratch (no personal data, nothing downloaded).

    python3 tests/fixtures.py OUTDIR [PORT]

writes sample.docx, sample.odt, sample.rtf, sample.txt, photo.docx (with a
photo that has EXIF and the rest in it), and these PDFs:
  pages.pdf    5 pages, "Page 1" ... "Page 5", with author/creator metadata
  form.pdf     a form: a text field and a check box
  hostile.pdf  JavaScript that runs on open and on page view, a launch action,
               a link and a remote "go to" that all point at http://127.0.0.1:PORT/
  refs-*.pdf   3 pages; bookmarks, links, structure tags, form fields, named
               destinations and the open action all point at page 2
  encrypted.pdf  protected with an owner password (opens without one)
  photo.pdf    photos with EXIF, XMP and the rest in them, and a reviewer's note
and photo-N.jpg (a JPEG full of metadata, EXIF orientation N) with plain-N.jpg
(the same picture without it).
"""
import hashlib
import io
import os
import struct
import sys
import zipfile
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import leaflet_formats as lf  # noqa: E402


def png_chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)


def png(width, height, rgb, before=b'', after=b''):
    """A plain single-colour PNG; `before` and `after` are more chunks, around the picture data."""
    raw = b''.join(b'\x00' + bytes(rgb) * width for _ in range(height))
    return (b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + before +
            png_chunk(b'IDAT', zlib.compress(raw)) + after + png_chunk(b'IEND', b''))


def jpeg_segment(marker, body):
    return bytes([0xFF, marker]) + struct.pack('>H', len(body) + 2) + body


def jpeg(width, height, segments=(), levels=(60, 200)):
    """A small grey JPEG, written out by hand: the left half dark, the right
    half light. `segments` go in right after the start of the file."""
    cols, rows = (width + 7) // 8, (height + 7) // 8
    bits = []
    previous = 0
    for _ in range(rows):
        for col in range(cols):
            dc = levels[0 if col < cols / 2 else 1] - 128      # one flat 8x8 block: only its average is stored
            diff, previous = dc - previous, dc
            size = abs(diff).bit_length()
            bits.append(f'{size:04b}')                          # the size, in the Huffman table below
            if size:
                bits.append(f'{diff if diff > 0 else diff + (1 << size) - 1:0{size}b}')
            bits.append('0')                                    # "nothing else in this block"
    stream = ''.join(bits)
    stream += '1' * (-len(stream) % 8)
    scan = bytes(int(stream[i:i + 8], 2) for i in range(0, len(stream), 8)).replace(b'\xff', b'\xff\x00')
    return (b'\xff\xd8' + b''.join(segments) +
            jpeg_segment(0xDB, b'\x00' + b'\x08' * 64) +
            jpeg_segment(0xC0, struct.pack('>BHHB', 8, height, width, 1) + b'\x01\x11\x00') +
            jpeg_segment(0xC4, b'\x00' + bytes([0, 0, 0, 9] + [0] * 12) + bytes(range(9))) +
            jpeg_segment(0xC4, b'\x10' + bytes([1] + [0] * 15) + b'\x00') +
            jpeg_segment(0xDA, b'\x01\x01\x00\x00\x3f\x00') + scan + b'\xff\xd9')


def exif(orientation, order='<'):
    """EXIF data the way a camera writes it: maker, which way up, and a GPS position."""
    maker = b'Sample Camera\x00'
    head = (b'II' if order == '<' else b'MM') + struct.pack(order + 'HI', 42, 8)
    after = 8 + 2 + 3 * 12 + 4
    ifd = struct.pack(order + 'H', 3)
    ifd += struct.pack(order + 'HHII', 0x010F, 2, len(maker), after)
    ifd += struct.pack(order + 'HHIHH', 0x0112, 3, 1, orientation, 0)
    ifd += struct.pack(order + 'HHII', 0x8825, 4, 1, after + len(maker))
    gps = struct.pack(order + 'H', 1) + struct.pack(order + 'HHI', 1, 2, 2) + b'N\x00\x00\x00' + struct.pack(order + 'I', 0)
    return head + ifd + struct.pack(order + 'I', 0) + maker + gps


JFIF = jpeg_segment(0xE0, b'JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00')
ICC = jpeg_segment(0xE2, b'ICC_PROFILE\x00\x01\x01' + b'sample colour profile')
ADOBE = jpeg_segment(0xEE, b'Adobe\x00\x64\x00\x00\x00\x00\x00')
# What a picture must not carry into a saved document.
PHOTO_MARKS = (b'Sample Camera', b'Sample Photographer', b'Sample caption', b'Sample comment', b'Sample thumbnail',
               b'Sample second picture', b'Sample trailing data', b'Sample Author', b'Sample private', b'GPS', b'N\x00\x00\x00')


def photo_jpeg(orientation=6, width=16, height=8):
    """A JPEG with everything a phone and a photo editor put into one."""
    return jpeg(width, height, [
        JFIF,
        jpeg_segment(0xE0, b'JFXX\x00\x13Sample thumbnail'),
        jpeg_segment(0xE1, b'Exif\x00\x00' + exif(orientation)),
        jpeg_segment(0xE1, b'http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta><dc:creator>Sample Photographer</dc:creator>'
                           b'<exif:GPSLatitude>GPS 1,2N</exif:GPSLatitude></x:xmpmeta>'),
        ICC,
        jpeg_segment(0xE2, b'MPF\x00Sample second picture'),
        jpeg_segment(0xED, b'Photoshop 3.0\x008BIM\x04\x04\x00\x00\x00\x00\x00\x10Sample caption'),
        ADOBE,
        jpeg_segment(0xFE, b'Sample comment'),
    ]) + b'Sample trailing data'


def plain_jpeg(orientation=6, width=16, height=8):
    """photo_jpeg() with only what the picture needs to look the same."""
    keep = [JFIF]
    if orientation != 1:
        keep.append(jpeg_segment(0xE1, b'Exif\x00\x00MM\x00\x2a\x00\x00\x00\x08\x00\x01\x01\x12\x00\x03\x00\x00\x00\x01' +
                                 bytes([0, orientation, 0, 0, 0, 0, 0, 0])))
    return jpeg(width, height, keep + [ICC, ADOBE])


PNG_COLOUR = png_chunk(b'gAMA', struct.pack('>I', 45455)) + png_chunk(b'pHYs', struct.pack('>IIB', 3780, 3780, 1))


def photo_png(orientation=6, order='>'):
    """A PNG with text, a date, EXIF and a program's own chunk in it."""
    before = (png_chunk(b'tEXt', b'Author\x00Sample Author') + PNG_COLOUR +
              png_chunk(b'iTXt', b'XML:com.adobe.xmp\x00\x00\x00\x00\x00<x:xmpmeta>Sample Photographer</x:xmpmeta>') +
              png_chunk(b'zTXt', b'Comment\x00\x00' + zlib.compress(b'Sample comment')) +
              png_chunk(b'tIME', struct.pack('>HBBBBB', 2020, 1, 1, 0, 0, 0)) +
              png_chunk(b'prVt', b'Sample private'))
    after = png_chunk(b'eXIf', exif(orientation, order)) + png_chunk(b'tEXt', b'Software\x00Sample Camera')
    return png(24, 12, (30, 120, 60), before, after) + b'Sample trailing data'


def plain_png(orientation=6):
    turned = b'' if orientation == 1 else png_chunk(
        b'eXIf', b'MM\x00\x2a\x00\x00\x00\x08\x00\x01\x01\x12\x00\x03\x00\x00\x00\x01' + bytes([0, orientation, 0, 0, 0, 0, 0, 0]))
    return png(24, 12, (30, 120, 60), PNG_COLOUR + turned)


def gif(extras=(), tail=b''):
    """A two-pixel GIF; `extras` are blocks in front of the picture."""
    return (b'GIF89a' + struct.pack('<HHBBB', 2, 1, 0x80, 0, 0) + b'\x00\x00\x00\xff\xff\xff' + b''.join(extras) +
            b'\x21\xf9\x04\x00\x00\x00\x00\x00' + b'\x2c' + struct.pack('<HHHHB', 0, 0, 2, 1, 0) +
            b'\x02\x02\x44\x0a\x00' + b';' + tail)


GIF_LOOP = b'\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00'


def photo_gif():
    return gif([GIF_LOOP, b'\x21\xfe\x0eSample comment\x00',
                b'\x21\xff\x0bXMP DataXMP\x13Sample Photographer\x00'], b'Sample trailing data')


def plain_gif():
    return gif([GIF_LOOP])


# ------------------------------------------------------------------ DOCX, the way Word writes it

W_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"')


def sample_docx():
    pic = png(40, 20, (200, 40, 40))
    document = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document {W_NS} mc:Ignorable="w14"><w:body>
<w:p><w:pPr><w:pStyle w:val="Titre1"/></w:pPr><w:r><w:t>Quarterly notes</w:t></w:r></w:p>
<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t xml:space="preserve">Plain, </w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>bold</w:t></w:r>
<w:r><w:t xml:space="preserve">, </w:t></w:r><w:r><w:rPr><w:i/></w:rPr><w:t>italic</w:t></w:r><w:r><w:t xml:space="preserve"> and </w:t></w:r>
<w:r><w:rPr><w:u w:val="single"/></w:rPr><w:t>underlined</w:t></w:r><w:r><w:t xml:space="preserve"> with </w:t></w:r>
<w:r><w:rPr><w:rStyle w:val="Strong"/></w:rPr><w:t>strong style</w:t></w:r><w:r><w:t>.</w:t></w:r></w:p>
<w:p><w:r><w:t xml:space="preserve">E = mc</w:t></w:r><w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>2</w:t></w:r>
<w:r><w:t xml:space="preserve"> and a </w:t></w:r><w:hyperlink r:id="rIdLink"><w:r><w:rPr><w:rStyle w:val="Hyperlink"/></w:rPr><w:t>link</w:t></w:r></w:hyperlink>
<w:r><w:t>.</w:t></w:r><w:commentRangeStart w:id="0"/><w:r><w:t xml:space="preserve"> Tab:</w:t></w:r><w:r><w:tab/><w:t>after</w:t></w:r><w:r><w:br/><w:t>new line</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="Titre2"/></w:pPr><w:r><w:t>Lists</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Apples</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Green ones</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Pears</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr><w:r><w:t>First step</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr><w:r><w:t>Second step</w:t></w:r></w:p>
<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/></w:tblPr><w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/></w:tblGrid>
<w:tr><w:tc><w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Item</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Count</w:t></w:r></w:p></w:tc></w:tr>
<w:tr><w:tc><w:p><w:r><w:t>Pens</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>12</w:t></w:r></w:p></w:tc></w:tr>
<w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>Spanning both</w:t></w:r></w:p></w:tc></w:tr>
</w:tbl>
<w:p><w:r><w:drawing><wp:inline><wp:extent cx="{80 * 9525}" cy="{40 * 9525}"/><wp:docPr id="1" name="Picture 1" descr="red box"/>
<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:blipFill><a:blip r:embed="rIdImg"/></pic:blipFill></pic:pic></a:graphicData></a:graphic>
</wp:inline></w:drawing></w:r></w:p>
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
<w:p><w:r><w:t>After the break.</w:t></w:r><w:r><w:footnoteReference w:id="1"/></w:r></w:p>
<w:sectPr><w:headerReference w:type="default" r:id="rIdHdr"/><w:pgSz w:w="11906" w:h="16838"/></w:sectPr>
</w:body></w:document>'''
    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Titre1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:rPr><w:b/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Titre2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="character" w:styleId="Strong"><w:name w:val="Strong"/><w:rPr><w:b/></w:rPr></w:style>
<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/><w:rPr><w:u w:val="single"/></w:rPr></w:style>
</w:styles>'''
    numbering = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl><w:lvl w:ilvl="1"><w:numFmt w:val="bullet"/></w:lvl></w:abstractNum>
<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/></w:lvl></w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>'''
    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rIdNum" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rIdImg" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>
<Relationship Id="rIdHdr" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>
<Relationship Id="rIdLink" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.org/" TargetMode="External"/>
</Relationships>'''
    core = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:creator>Sample Author</dc:creator><cp:lastModifiedBy>Sample Editor</cp:lastModifiedBy></cp:coreProperties>'''
    return lf.make_zip([
        ('[Content_Types].xml', lf.DOCX_CONTENT_TYPES),
        ('_rels/.rels', lf.DOCX_ROOT_RELS),
        ('docProps/core.xml', core),
        ('word/document.xml', document),
        ('word/styles.xml', styles),
        ('word/numbering.xml', numbering),
        ('word/header1.xml', f'<w:hdr {W_NS}><w:p><w:r><w:t>Header</w:t></w:r></w:p></w:hdr>'),
        ('word/_rels/document.xml.rels', rels),
        ('word/media/image1.png', pic),
    ])


# ------------------------------------------------------------------ ODT, the way LibreOffice writes it

def sample_odt():
    pic = png(30, 30, (40, 90, 200))
    content = f'''<?xml version="1.0" encoding="UTF-8"?>
<office:document-content {lf.ODT_NS} office:version="1.3">
<office:automatic-styles>
<style:style style:name="P1" style:family="paragraph" style:parent-style-name="Standard"><style:paragraph-properties fo:text-align="end"/></style:style>
<style:style style:name="P2" style:family="paragraph" style:parent-style-name="Standard"><style:paragraph-properties fo:break-before="page"/></style:style>
<style:style style:name="T1" style:family="text"><style:text-properties fo:font-weight="bold"/></style:style>
<style:style style:name="T2" style:family="text"><style:text-properties fo:font-style="italic" style:text-underline-style="solid"/></style:style>
<style:style style:name="T3" style:family="text"><style:text-properties style:text-position="-33% 58%"/></style:style>
<text:list-style style:name="L1"><text:list-level-style-bullet text:level="1" text:bullet-char="•"/><text:list-level-style-number text:level="2" style:num-format="1"/></text:list-style>
<text:list-style style:name="L2"><text:list-level-style-number text:level="1" style:num-format="1"/></text:list-style>
</office:automatic-styles>
<office:body><office:text>
<text:sequence-decls/>
<text:h text:style-name="Heading_20_1" text:outline-level="1">Trip plan</text:h>
<text:p text:style-name="P1">Right aligned with <text:span text:style-name="T1">bold</text:span> and <text:span text:style-name="T2">italic underline</text:span>.</text:p>
<text:p text:style-name="Standard">Water is H<text:span text:style-name="T3">2</text:span>O,<text:s text:c="2"/>spaced<text:tab/>tabbed<text:line-break/>broken. <text:a xlink:type="simple" xlink:href="https://example.org/">A link</text:a><office:annotation><text:p>note</text:p></office:annotation></text:p>
<text:list text:style-name="L1"><text:list-item><text:p>Tent</text:p><text:list><text:list-item><text:p>Poles</text:p></text:list-item></text:list></text:list-item><text:list-item><text:p>Stove</text:p></text:list-item></text:list>
<text:list text:style-name="L2"><text:list-item><text:p>Drive</text:p></text:list-item><text:list-item><text:p>Walk</text:p></text:list-item></text:list>
<table:table table:name="Table1"><table:table-column table:number-columns-repeated="2"/>
<table:table-header-rows><table:table-row><table:table-cell office:value-type="string"><text:p>Day</text:p></table:table-cell><table:table-cell office:value-type="string"><text:p>Place</text:p></table:table-cell></table:table-row></table:table-header-rows>
<table:table-row><table:table-cell office:value-type="string"><text:p>1</text:p></table:table-cell><table:table-cell office:value-type="string"><text:p>Lake</text:p></table:table-cell></table:table-row>
</table:table>
<text:p text:style-name="Standard"><draw:frame draw:name="Image1" text:anchor-type="as-char" svg:width="0.794cm" svg:height="0.794cm"><draw:image xlink:href="Pictures/blue.png" xlink:type="simple"/><svg:desc>blue square</svg:desc></draw:frame></text:p>
<text:p text:style-name="P2">Second page.</text:p>
</office:text></office:body></office:document-content>'''
    styles = f'''<?xml version="1.0" encoding="UTF-8"?>
<office:document-styles {lf.ODT_NS} office:version="1.3"><office:styles>
<style:style style:name="Standard" style:family="paragraph"/>
<style:style style:name="Heading_20_1" style:family="paragraph" style:default-outline-level="1"/>
</office:styles></office:document-styles>'''
    meta = '''<?xml version="1.0" encoding="UTF-8"?>
<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
<office:meta><dc:creator>Sample Author</dc:creator><meta:generator>SomeOffice/1.0</meta:generator></office:meta></office:document-meta>'''
    manifest = '''<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.3">
<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>
<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>
<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>
<manifest:file-entry manifest:full-path="Pictures/blue.png" manifest:media-type="image/png"/>
</manifest:manifest>'''
    return lf.make_zip([('mimetype', 'application/vnd.oasis.opendocument.text'), ('content.xml', content),
                        ('styles.xml', styles), ('meta.xml', meta), ('META-INF/manifest.xml', manifest),
                        ('Pictures/blue.png', pic)], stored_first='mimetype')


SAMPLE_RTF = (rb'{\rtf1\ansi\ansicpg1252\deff0{\fonttbl{\f0 Times;}}{\info{\author Sample Author}}'
              rb'\pard\qc Hello \b bold\b0  and \i italic\i0  caf\'e9 \u8364?.\par'
              rb'\pard Second line\line same paragraph.\par}')


# ------------------------------------------------------------------ PDFs, written by hand

class PdfWriter:
    def __init__(self):
        self.objects = []

    def add(self, body):
        self.objects.append(body if isinstance(body, bytes) else body.encode('latin-1'))
        return len(self.objects)

    def set(self, num, body):
        self.objects[num - 1] = body if isinstance(body, bytes) else body.encode('latin-1')

    def stream(self, data, extra=''):
        return self.add(f'<< /Length {len(data)} {extra}>>\nstream\n'.encode() + data + b'\nendstream')

    def bytes(self, root, info=None, trailer=''):
        out = bytearray(b'%PDF-1.7\n%\xe2\xe3\xcf\xd3\n')
        offsets = []
        for n, body in enumerate(self.objects, 1):
            offsets.append(len(out))
            out += f'{n} 0 obj\n'.encode() + body + b'\nendobj\n'
        xref = len(out)
        out += f'xref\n0 {len(self.objects) + 1}\n0000000000 65535 f \n'.encode()
        for off in offsets:
            out += f'{off:010d} 00000 n \n'.encode()
        info_ref = f' /Info {info} 0 R' if info else ''
        out += f'trailer\n<< /Size {len(self.objects) + 1} /Root {root} 0 R{info_ref}{trailer} >>\nstartxref\n{xref}\n%%EOF\n'.encode()
        return bytes(out)


def text_page(w, pages_ref, font_ref, text, extra=''):
    content = f'BT /F1 36 Tf 72 700 Td ({text}) Tj ET'.encode()
    c = w.stream(content)
    return w.add(f'<< /Type /Page /Parent {pages_ref} 0 R /MediaBox [0 0 612 792] '
                 f'/Resources << /Font << /F1 {font_ref} 0 R >> >> /Contents {c} 0 R {extra}>>')


def pages_pdf(count=5, label='Page'):
    w = PdfWriter()
    pages = w.add('')       # filled in below
    font = w.add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    kids = [text_page(w, pages, font, f'{label} {n}') for n in range(1, count + 1)]
    w.set(pages, f'<< /Type /Pages /Kids [{" ".join(f"{k} 0 R" for k in kids)}] /Count {len(kids)} >>')
    xmp = w.stream(b'<?xpacket begin=""?><x:xmpmeta xmlns:x="adobe:ns:meta/"><dc:creator>Sample Author</dc:creator>'
                   b'</x:xmpmeta><?xpacket end="w"?>', '/Type /Metadata /Subtype /XML ')
    root = w.add(f'<< /Type /Catalog /Pages {pages} 0 R /Metadata {xmp} 0 R >>')
    info = w.add('<< /Title (Sample pages) /Author (Sample Author) /Creator (Sample Writer 1.0) '
                 '/Producer (Sample Producer) /CreationDate (D:20200101000000Z) >>')
    return w.bytes(root, info)


def form_pdf():
    w = PdfWriter()
    pages = w.add('')
    font = w.add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    page = w.add('')
    field = w.add(f'<< /Type /Annot /Subtype /Widget /FT /Tx /T (name) /V () /Rect [72 600 372 630] '
                  f'/P {page} 0 R /F 4 /DA (/Helv 14 Tf 0 g) /MK << /BG [0.9 0.9 1] /BC [0 0 0] >> /M (D:20200101000000Z) >>')
    on = w.stream(b'0 g 2 2 12 12 re f', '/Type /XObject /Subtype /Form /BBox [0 0 16 16] ')
    off = w.stream(b'', '/Type /XObject /Subtype /Form /BBox [0 0 16 16] ')
    check = w.add(f'<< /Type /Annot /Subtype /Widget /FT /Btn /T (agree) /V /Off /AS /Off /Rect [72 540 88 556] '
                  f'/P {page} 0 R /F 4 /MK << /BC [0 0 0] /CA (4) >> /AP << /N << /Yes {on} 0 R /Off {off} 0 R >> >> >>')
    content = w.stream(b'BT /F1 18 Tf 72 700 Td (Form: name and agreement) Tj ET '
                       b'BT /F1 12 Tf 72 640 Td (Name) Tj ET BT /F1 12 Tf 96 544 Td (I agree) Tj ET')
    w.set(page, f'<< /Type /Page /Parent {pages} 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {font} 0 R >> >> '
                f'/Contents {content} 0 R /Annots [{field} 0 R {check} 0 R] >>')
    w.set(pages, f'<< /Type /Pages /Kids [{page} 0 R] /Count 1 >>')
    helv = w.add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>')
    root = w.add(f'<< /Type /Catalog /Pages {pages} 0 R /AcroForm << /Fields [{field} 0 R {check} 0 R] '
                 f'/DA (/Helv 12 Tf 0 g) /DR << /Font << /Helv {helv} 0 R >> >> /NeedAppearances true >> >>')
    return w.bytes(root)


# Everything that belongs to page 2 of refs_pdf() carries this, so a test can
# tell whether anything of the page is left in a file after it's deleted.
REMOVED_MARK = 'ZQXJ'
REMOVED_IMAGE = b'IMGZQXJ!' * 8
REF_KINDS = ('outline', 'link', 'struct', 'form', 'names', 'open')


def refs_pdf(kinds=REF_KINDS):
    """Three pages, with the kinds of reference to page 2 that are asked for."""
    w = PdfWriter()
    pages = w.add('')
    font = w.add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    p1, p2, p3 = w.add(''), w.add(''), w.add('')
    image = w.stream(REMOVED_IMAGE, '/Type /XObject /Subtype /Image /Width 8 /Height 8 /ColorSpace /DeviceGray '
                                    '/BitsPerComponent 8 ')
    body = zlib.compress(f'BT /F1 24 Tf 72 700 Td (Removed page {REMOVED_MARK}) Tj ET '
                         'q 64 0 0 64 72 500 cm /Im1 Do Q'.encode())
    contents = {p1: w.stream(b'BT /F1 24 Tf 72 700 Td (Kept 1) Tj ET'), p2: w.stream(body, '/Filter /FlateDecode '),
                p3: w.stream(b'BT /F1 24 Tf 72 700 Td (Kept 3) Tj ET')}
    annots = {p1: [], p2: [], p3: []}
    catalog = [f'/Type /Catalog /Pages {pages} 0 R']
    note = w.add(f'<< /Type /Annot /Subtype /Text /Rect [72 400 92 420] /P {p2} 0 R /Contents (Removed note {REMOVED_MARK}) >>')
    annots[p2].append(note)

    if 'names' in kinds:
        leaf = w.add(f'<< /Limits [(to2) (to3)] /Names [(to2) [{p2} 0 R /Fit] (to3) << /D [{p3} 0 R /Fit] >>] >>')
        tree = w.add(f'<< /Kids [{leaf} 0 R] >>')
        catalog.append(f'/Names << /Dests {tree} 0 R >> /Dests << /old2 [{p2} 0 R /Fit] /old3 [{p3} 0 R /Fit] >>')

    if 'outline' in kinds:
        root = w.add('')
        named = '/Dest (to2)' if 'names' in kinds else f'/Dest [{p2} 0 R /Fit]'
        # (title, where it goes, children)
        items = [('Kept one', f'/Dest [{p1} 0 R /Fit]', []),
                 (f'Removed chapter {REMOVED_MARK}', f'/Dest [{p2} 0 R /XYZ 0 792 0]',
                  [(f'Removed section {REMOVED_MARK}', f'/A << /S /GoTo /D [{p2} 0 R /Fit] >>', [])]),
                 (f'Removed by name {REMOVED_MARK}', named, []),
                 ('Kept group', '', [(f'Removed leaf {REMOVED_MARK}', f'/Dest [{p2} 0 R /Fit]', []),
                                     ('Kept leaf', f'/Dest [{p3} 0 R /Fit]', [])]),
                 ('Kept three', f'/Dest [{p3} 0 R /Fit]', [])]

        def level(entries, parent):
            refs = [w.add('') for _ in entries]
            for n, (ref, (title, dest, kids)) in enumerate(zip(refs, entries)):
                links = f'/Parent {parent} 0 R'
                if n:
                    links += f' /Prev {refs[n - 1]} 0 R'
                if n + 1 < len(refs):
                    links += f' /Next {refs[n + 1]} 0 R'
                if kids:
                    below = level(kids, ref)
                    links += f' /First {below[0]} 0 R /Last {below[-1]} 0 R /Count {len(below)}'
                w.set(ref, f'<< /Title ({title}) {dest} {links} >>')
            return refs
        top = level(items, root)
        w.set(root, f'<< /Type /Outlines /First {top[0]} 0 R /Last {top[-1]} 0 R /Count 8 >>')
        catalog.append(f'/Outlines {root} 0 R')

    if 'link' in kinds:
        link = '<< /Type /Annot /Subtype /Link /Rect [72 %d 300 %d] /Border [0 0 1] %s >>'
        annots[p1].append(w.add(link % (600, 620, f'/Dest [{p2} 0 R /Fit]')))
        annots[p1].append(w.add(link % (560, 580, f'/A << /S /GoTo /D [{p3} 0 R /Fit] >>')))
        annots[p3].append(w.add(link % (600, 620, f'/A << /S /GoTo /D [{p2} 0 R /Fit] >>')))
        annots[p3].append(w.add(link % (560, 580, f'/Dest [{p1} 0 R /Fit]')))

    if 'form' in kinds:
        def field(page, y, rest):
            ref = w.add(f'<< /Type /Annot /Subtype /Widget /Rect [72 {y} 372 {y + 30}] /P {page} 0 R /F 4 '
                        f'/DA (/Helv 14 Tf 0 g) {rest} >>')
            annots[page].append(ref)
            return ref
        kept = field(p1, 300, '/FT /Tx /T (kept) /V (Kept value)')
        gone = field(p2, 300, f'/FT /Tx /T (gone) /V (Removed value {REMOVED_MARK})')
        group = w.add('')
        kid1 = field(p1, 200, f'/Parent {group} 0 R')
        kid2 = field(p2, 200, f'/Parent {group} 0 R')
        w.set(group, f'<< /FT /Tx /T (both) /V (On two pages) /Kids [{kid1} 0 R {kid2} 0 R] >>')
        alone = w.add('')
        kid3 = field(p2, 100, f'/Parent {alone} 0 R')
        w.set(alone, f'<< /FT /Tx /T (alone) /V (Removed group {REMOVED_MARK}) /Kids [{kid3} 0 R] >>')
        helv = w.add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>')
        catalog.append(f'/AcroForm << /Fields [{kept} 0 R {gone} 0 R {group} 0 R {alone} 0 R] /CO [{gone} 0 R {kept} 0 R] '
                       f'/DA (/Helv 12 Tf 0 g) /DR << /Font << /Helv {helv} 0 R >> >> >>')

    struct_parents = {}
    if 'struct' in kinds:
        root, doc = w.add(''), w.add('')
        e1 = w.add(f'<< /Type /StructElem /S /P /P {doc} 0 R /Pg {p1} 0 R /K 0 >>')
        e2 = w.add(f'<< /Type /StructElem /S /P /P {doc} 0 R /Pg {p2} 0 R /K [0 << /Type /MCR /Pg {p2} 0 R /MCID 1 >>] '
                   f'/ActualText (Removed text {REMOVED_MARK}) >>')
        sect = w.add('')
        span = w.add(f'<< /Type /StructElem /S /Span /P {sect} 0 R /Pg {p2} 0 R /K 2 /Alt (Removed alt {REMOVED_MARK}) >>')
        w.set(sect, f'<< /Type /StructElem /S /Sect /P {doc} 0 R /T (Removed part {REMOVED_MARK}) /K {span} 0 R >>')
        e3 = w.add(f'<< /Type /StructElem /S /P /P {doc} 0 R /Pg {p3} 0 R /K 0 >>')
        e4 = w.add(f'<< /Type /StructElem /S /Annot /P {doc} 0 R /T (Removed tag {REMOVED_MARK}) '
                   f'/K << /Type /OBJR /Obj {note} 0 R >> >>')
        mixed = w.add(f'<< /Type /StructElem /S /Div /P {doc} 0 R /Pg {p2} 0 R '
                      f'/K [3 << /Type /MCR /Pg {p3} 0 R /MCID 1 >>] >>')
        w.set(doc, f'<< /Type /StructElem /S /Document /P {root} 0 R '
                   f'/K [{e1} 0 R {e2} 0 R {sect} 0 R {e3} 0 R {e4} 0 R {mixed} 0 R] >>')
        tree = w.add(f'<< /Nums [0 [{e1} 0 R] 1 [{e2} 0 R {e2} 0 R {span} 0 R {mixed} 0 R] 2 [{e3} 0 R {mixed} 0 R] 3 {e4} 0 R] >>')
        w.set(root, f'<< /Type /StructTreeRoot /K {doc} 0 R /ParentTree {tree} 0 R /ParentTreeNextKey 4 >>')
        w.set(note, f'<< /Type /Annot /Subtype /Text /Rect [72 400 92 420] /Contents (Removed note {REMOVED_MARK}) '
                    '/StructParent 3 >>')
        catalog.append(f'/StructTreeRoot {root} 0 R /MarkInfo << /Marked true >>')
        struct_parents = {p1: 0, p2: 1, p3: 2}

    if 'open' in kinds:
        catalog.append(f'/OpenAction [{p2} 0 R /Fit]')

    for page in (p1, p2, p3):
        extra = f'/Annots [{" ".join(f"{a} 0 R" for a in annots[page])}] ' if annots[page] else ''
        if page in struct_parents:
            extra += f'/StructParents {struct_parents[page]} '
        xobject = f'/XObject << /Im1 {image} 0 R >> ' if page == p2 else ''
        w.set(page, f'<< /Type /Page /Parent {pages} 0 R /MediaBox [0 0 612 792] '
                    f'/Resources << /Font << /F1 {font} 0 R >> {xobject}>> /Contents {contents[page]} 0 R {extra}>>')
    w.set(pages, f'<< /Type /Pages /Kids [{p1} 0 R {p2} 0 R {p3} 0 R] /Count 3 >>')
    return w.bytes(w.add(f'<< {" ".join(catalog)} >>'))


def photo_docx():
    """sample.docx with a photo straight from a camera where its picture was."""
    with zipfile.ZipFile(io.BytesIO(sample_docx())) as z:
        return lf.make_zip([(name, photo_jpeg(6) if name == 'word/media/image1.png' else z.read(name))
                            for name in z.namelist()])


def photo_pdf():
    """One page with photos as a scanner or a phone app puts them in (EXIF and
    all, and an XMP packet of their own), and a reviewer's note."""
    w = PdfWriter()
    pages = w.add('')
    font = w.add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    page = w.add('')
    xmp = w.stream(b'<?xpacket begin=""?><x:xmpmeta xmlns:x="adobe:ns:meta/"><dc:creator>Sample Photographer</dc:creator>'
                   b'</x:xmpmeta><?xpacket end="w"?>', '/Type /Metadata /Subtype /XML ')
    private = w.add('<< /SampleApp << /LastModified (D:20200101000000Z) /Private (Sample private) >> >>')
    image = '/Type /XObject /Subtype /Image /Width 16 /Height 8 /ColorSpace /DeviceGray /BitsPerComponent 8 '
    im1 = w.stream(photo_jpeg(6), f'{image}/Filter /DCTDecode /Metadata {xmp} 0 R /PieceInfo {private} 0 R '
                                  '/LastModified (D:20200101000000Z) ')
    im2 = w.stream(photo_jpeg(1), f'{image}/Filter [/DCTDecode] ')
    popup = w.add('')
    note = w.add(f'<< /Type /Annot /Subtype /Text /Rect [72 400 92 420] /Contents (A note that stays) /T (Sample Reviewer) '
                 f'/NM (sample-note-id) /M (D:20200101000000Z) /CreationDate (D:20200101000000Z) /Popup {popup} 0 R >>')
    w.set(popup, f'<< /Type /Annot /Subtype /Popup /Rect [100 300 300 400] /Parent {note} 0 R /T (Sample Reviewer) '
                 '/M (D:20200101000000Z) >>')
    reply = w.add(f'<< /Type /Annot /Subtype /FreeText /Rect [72 300 300 330] /Contents (A reply that stays) '
                  f'/T (Sample Reviewer) /IRT {note} 0 R /DA (/Helv 12 Tf 0 g) /CreationDate (D:20200101000000Z) >>')
    field = w.add(f'<< /Type /Annot /Subtype /Widget /FT /Tx /T (name) /V (A value) /Rect [72 600 372 630] '
                  f'/P {page} 0 R /F 4 /DA (/Helv 14 Tf 0 g) /M (D:20200101000000Z) >>')
    content = w.stream(b'BT /F1 18 Tf 72 700 Td (Two photos) Tj ET q 160 0 0 80 72 480 cm /Im1 Do Q '
                       b'q 160 0 0 80 300 480 cm /Im2 Do Q')
    w.set(page, f'<< /Type /Page /Parent {pages} 0 R /MediaBox [0 0 612 792] /Contents {content} 0 R '
                f'/Resources << /Font << /F1 {font} 0 R >> /XObject << /Im1 {im1} 0 R /Im2 {im2} 0 R >> >> '
                f'/Annots [{note} 0 R {popup} 0 R {reply} 0 R {field} 0 R] /LastModified (D:20200101000000Z) >>')
    w.set(pages, f'<< /Type /Pages /Kids [{page} 0 R] /Count 1 >>')
    helv = w.add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>')
    root = w.add(f'<< /Type /Catalog /Pages {pages} 0 R /AcroForm << /Fields [{field} 0 R] '
                 f'/DA (/Helv 12 Tf 0 g) /DR << /Font << /Helv {helv} 0 R >> >> >> >>')
    return w.bytes(root)


def rc4(key, data):
    box = list(range(256))
    j = 0
    for i in range(256):
        j = (j + box[i] + key[i % len(key)]) & 255
        box[i], box[j] = box[j], box[i]
    out = bytearray()
    i = j = 0
    for byte in data:
        i = (i + 1) & 255
        j = (j + box[i]) & 255
        box[i], box[j] = box[j], box[i]
        out.append(byte ^ box[(box[i] + box[j]) & 255])
    return bytes(out)


def encrypted_pdf():
    """A PDF protected the old, simple way (40-bit RC4) with an owner password
    and no password needed to open it; it has an author in its document info."""
    pad = bytes.fromhex('28bf4e5e4e758a4164004e56fffa01082e2e00b6d0683e802f0ca9fe6453697a')
    file_id = hashlib.md5(b'leaflet test file').digest()
    permissions = struct.pack('<i', -44)
    owner = rc4(hashlib.md5((b'owner' + pad)[:32]).digest()[:5], pad)
    key = hashlib.md5(pad + owner + permissions + file_id).digest()[:5]
    user = rc4(key, pad)

    def secret(num, data):
        return rc4(hashlib.md5(key + struct.pack('<I', num)[:3] + b'\x00\x00').digest()[:10], data)
    w = PdfWriter()
    pages = w.add('')
    font = w.add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    content = len(w.objects) + 1
    w.stream(secret(content, b'BT /F1 24 Tf 72 700 Td (An encrypted page) Tj ET'))
    page = w.add(f'<< /Type /Page /Parent {pages} 0 R /MediaBox [0 0 612 792] '
                 f'/Resources << /Font << /F1 {font} 0 R >> >> /Contents {content} 0 R >>')
    w.set(pages, f'<< /Type /Pages /Kids [{page} 0 R] /Count 1 >>')
    root = w.add(f'<< /Type /Catalog /Pages {pages} 0 R >>')
    info = len(w.objects) + 1
    w.add(f'<< /Title <{secret(info, b"Encrypted sample").hex()}> /Author <{secret(info, b"Sample Author").hex()}> >>')
    encrypt = w.add(f'<< /Filter /Standard /V 1 /R 2 /O <{owner.hex()}> /U <{user.hex()}> /P -44 >>')
    return w.bytes(root, info, f' /Encrypt {encrypt} 0 R /ID [<{file_id.hex()}> <{file_id.hex()}>]')


def hostile_pdf(port):
    url = f'http://127.0.0.1:{port}'
    w = PdfWriter()
    pages = w.add('')
    font = w.add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    js = (f"app.alert('pdf-js-ran'); this.submitForm('{url}/submit'); app.launchURL('{url}/launch', true); "
          f"this.getURL('{url}/geturl');")
    open_js = w.add(f'<< /S /JavaScript /JS ({js}) >>')
    names_js = w.add(f'<< /S /JavaScript /JS (app.alert\\("names-js-ran"\\); this.getURL\\("{url}/names"\\);) >>')
    page = w.add('')
    link = w.add(f'<< /Type /Annot /Subtype /Link /Rect [72 600 400 640] /Border [0 0 1] '
                 f'/A << /S /URI /URI ({url}/link) >> >>')
    remote = w.add(f'<< /Type /Annot /Subtype /Link /Rect [72 540 400 580] /Border [0 0 1] '
                   f'/A << /S /GoToR /F << /FS /URL /F ({url}/remote.pdf) >> /D [0 /Fit] >> >>')
    launch = w.add('<< /Type /Annot /Subtype /Link /Rect [72 480 400 520] /Border [0 0 1] '
                   '/A << /S /Launch /F (/bin/sh) >> >>')
    content = w.stream(b'BT /F1 18 Tf 72 700 Td (This file tries to run JavaScript) Tj ET '
                       b'BT /F1 14 Tf 72 615 Td (A web link) Tj ET BT /F1 14 Tf 72 555 Td (A remote PDF) Tj ET '
                       b'BT /F1 14 Tf 72 495 Td (A launch action) Tj ET')
    w.set(page, f'<< /Type /Page /Parent {pages} 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {font} 0 R >> >> '
                f'/Contents {content} 0 R /Annots [{link} 0 R {remote} 0 R {launch} 0 R] '
                f'/AA << /O << /S /JavaScript /JS (this.getURL\\("{url}/pageopen"\\);) >> >> >>')
    w.set(pages, f'<< /Type /Pages /Kids [{page} 0 R] /Count 1 >>')
    names = w.add(f'<< /JavaScript << /Names [(a) {names_js} 0 R] >> >>')
    root = w.add(f'<< /Type /Catalog /Pages {pages} 0 R /OpenAction {open_js} 0 R /Names {names} 0 R '
                 f'/URI << /Base ({url}/) >> >>')
    return w.bytes(root)


def write_all(out, port=8765):
    os.makedirs(out, exist_ok=True)
    files = {
        'sample.docx': sample_docx(), 'sample.odt': sample_odt(), 'sample.rtf': SAMPLE_RTF,
        'sample.txt': 'First line\nSecond line\n\nAfter an empty line\n'.encode(),
        'pages.pdf': pages_pdf(), 'extra.pdf': pages_pdf(2, 'Extra'), 'form.pdf': form_pdf(),
        'hostile.pdf': hostile_pdf(port), 'refs-all.pdf': refs_pdf(), 'photo.pdf': photo_pdf(), 'photo.docx': photo_docx(),
        'encrypted.pdf': encrypted_pdf(),
        'photo-1.jpg': photo_jpeg(1), 'plain-1.jpg': plain_jpeg(1), 'photo-6.jpg': photo_jpeg(6), 'plain-6.jpg': plain_jpeg(6),
    }
    for kind in REF_KINDS:
        files[f'refs-{kind}.pdf'] = refs_pdf((kind,))
    for name, data in files.items():
        with open(os.path.join(out, name), 'wb') as f:
            f.write(data)
    return sorted(files)


if __name__ == '__main__':
    print(' '.join(write_all(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 8765)))
