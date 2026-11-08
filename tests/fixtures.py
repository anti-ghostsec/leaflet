"""Test files for Leaflet, made from scratch (no personal data, nothing downloaded).

    python3 tests/fixtures.py OUTDIR [PORT]

writes sample.docx, sample.odt, sample.rtf, sample.txt, and these PDFs:
  pages.pdf    5 pages, "Page 1" ... "Page 5", with author/creator metadata
  form.pdf     a form: a text field and a check box
  hostile.pdf  JavaScript that runs on open and on page view, a launch action,
               a link and a remote "go to" that all point at http://127.0.0.1:PORT/
"""
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import leaflet_formats as lf  # noqa: E402


def png(width, height, rgb):
    """A plain single-colour PNG."""
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    raw = b''.join(b'\x00' + bytes(rgb) * width for _ in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


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

    def bytes(self, root, info=None):
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
        out += f'trailer\n<< /Size {len(self.objects) + 1} /Root {root} 0 R{info_ref} >>\nstartxref\n{xref}\n%%EOF\n'.encode()
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
        'hostile.pdf': hostile_pdf(port),
    }
    for name, data in files.items():
        with open(os.path.join(out, name), 'wb') as f:
            f.write(data)
    return sorted(files)


if __name__ == '__main__':
    print(' '.join(write_all(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 8765)))
