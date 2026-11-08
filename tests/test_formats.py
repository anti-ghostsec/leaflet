"""Open/save round trips for DOCX, ODT, RTF and text.   python3 -m unittest discover tests"""
import io
import os
import sys
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
import fixtures  # noqa: E402
import leaflet_formats as lf  # noqa: E402
from leaflet_formats import Break, Image, ListBlock, PageBreak, Para, Table, Text  # noqa: E402


def text_of(block):
    return ''.join(r.text for r in block.runs if isinstance(r, Text))


def outline(blocks):
    """A compact picture of the structure, for comparing two documents."""
    out = []
    for b in blocks:
        if isinstance(b, Para):
            runs = []
            for r in b.runs:
                if isinstance(r, Text):
                    flags = ''.join(k for k in ('b', 'i', 'u', 's', 'sup', 'sub') if getattr(r, k))
                    runs.append((r.text, flags, r.href))
                elif isinstance(r, Image):
                    runs.append(('<img>', r.mime, len(r.data)))
                elif isinstance(r, Break):
                    runs.append(('<br>',))
            out.append(('p', b.style, b.align, tuple(runs)))
        elif isinstance(b, ListBlock):
            out.append(('list', b.ordered, tuple(tuple(outline(item)) for item in b.items)))
        elif isinstance(b, Table):
            out.append(('table', tuple(tuple((c.colspan, tuple(outline(c.blocks))) for c in row) for row in b.rows)))
        elif isinstance(b, PageBreak):
            out.append(('pagebreak',))
    return out


class DocxTest(unittest.TestCase):
    def setUp(self):
        self.doc = lf.read_docx(fixtures.sample_docx())
        self.blocks = self.doc.blocks

    def test_reads_word_structure(self):
        b = self.blocks
        self.assertEqual((b[0].style, text_of(b[0])), ('h1', 'Quarterly notes'))   # localized style id
        self.assertEqual(b[1].align, 'center')
        fmt = {r.text: r for r in b[1].runs}
        self.assertTrue(fmt['bold'].b and not fmt['bold'].i)
        self.assertTrue(fmt['italic'].i)
        self.assertTrue(fmt['underlined'].u)
        self.assertTrue(fmt['strong style'].b)       # bold from a character style
        runs = b[2].runs
        self.assertTrue(any(isinstance(r, Text) and r.sup and r.text == '2' for r in runs))
        self.assertTrue(any(isinstance(r, Text) and r.href == 'https://example.org/' for r in runs))
        self.assertTrue(any(isinstance(r, Break) for r in runs))
        self.assertIn('\t', text_of(b[2]))
        self.assertEqual(b[3].style, 'h2')
        bullets, steps = b[4], b[5]
        self.assertFalse(bullets.ordered)
        self.assertEqual([text_of(i[0]) for i in bullets.items], ['Apples', 'Pears'])
        self.assertEqual(text_of(bullets.items[0][1].items[0][0]), 'Green ones')   # nested
        self.assertTrue(steps.ordered)
        self.assertEqual(len(steps.items), 2)
        table = b[6]
        self.assertIsInstance(table, Table)
        self.assertEqual(len(table.rows), 3)
        self.assertEqual(table.rows[2][0].colspan, 2)
        img = [r for r in b[7].runs if isinstance(r, Image)][0]
        self.assertEqual((img.mime, img.width, img.height, img.alt), ('image/png', 80, 40, 'red box'))
        self.assertTrue(any(isinstance(x, PageBreak) for x in b))

    def test_warns_about_what_it_drops(self):
        w = ' '.join(self.doc.warnings)
        for word in ('comments', 'headers', 'footnotes'):
            self.assertIn(word, w)

    def test_round_trip_docx(self):
        again = lf.read_docx(lf.write_docx(self.doc))
        self.assertEqual(outline(again.blocks), outline(self.blocks))
        self.assertEqual(again.warnings, [])

    def test_round_trip_through_editor_html(self):
        again = lf.from_html(lf.to_html(self.doc))
        self.assertEqual(outline(again.blocks), outline(self.blocks))

    def test_docx_to_odt_and_back(self):
        again = lf.read_odt(lf.write_odt(self.doc))
        self.assertEqual(outline(again.blocks), outline(self.blocks))

    def test_no_metadata_written(self):
        data = lf.write_docx(self.doc)
        z = zipfile.ZipFile(io.BytesIO(data))
        names = z.namelist()
        self.assertFalse([n for n in names if n.startswith('docProps')])
        self.assertTrue(all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in z.infolist()))
        self.assertNotIn(b'Sample Author', b''.join(z.read(n) for n in names))
        self.assertEqual(lf.detect(data), 'docx')

    def test_saving_is_repeatable(self):
        self.assertEqual(lf.write_docx(self.doc), lf.write_docx(lf.read_docx(lf.write_docx(self.doc))))


class OdtTest(unittest.TestCase):
    def setUp(self):
        self.doc = lf.read_odt(fixtures.sample_odt())
        self.blocks = self.doc.blocks

    def test_reads_libreoffice_structure(self):
        b = self.blocks
        self.assertEqual((b[0].style, text_of(b[0])), ('h1', 'Trip plan'))
        self.assertEqual(b[1].align, 'right')
        fmt = {r.text: r for r in b[1].runs}
        self.assertTrue(fmt['bold'].b)
        self.assertTrue(fmt['italic underline'].i and fmt['italic underline'].u)
        p = b[2]
        self.assertTrue(any(isinstance(r, Text) and r.sub and r.text == '2' for r in p.runs))
        self.assertIn('O,  spaced\ttabbed', text_of(p))
        self.assertTrue(any(isinstance(r, Break) for r in p.runs))
        self.assertTrue(any(isinstance(r, Text) and r.href == 'https://example.org/' for r in p.runs))
        tent = b[3]
        self.assertFalse(tent.ordered)
        self.assertTrue(tent.items[0][1].ordered)       # level 2 of L1 is numbered
        self.assertTrue(b[4].ordered)
        self.assertEqual(len(b[5].rows), 2)             # header row + body row
        img = [r for r in b[6].runs if isinstance(r, Image)][0]
        self.assertEqual((img.width, img.alt), (30, 'blue square'))
        self.assertIsInstance(b[7], PageBreak)
        self.assertEqual(text_of(b[8]), 'Second page.')
        self.assertIn('comments', ' '.join(self.doc.warnings))

    def test_round_trip_odt(self):
        data = lf.write_odt(self.doc)
        again = lf.read_odt(data)
        self.assertEqual(outline(again.blocks), outline(self.blocks))
        z = zipfile.ZipFile(io.BytesIO(data))
        first = z.infolist()[0]
        self.assertEqual((first.filename, first.compress_type), ('mimetype', zipfile.ZIP_STORED))
        self.assertNotIn('meta.xml', z.namelist())
        self.assertNotIn(b'Sample Author', data)
        self.assertEqual(lf.detect(data), 'odt')

    def test_odt_to_docx_and_back(self):
        again = lf.read_docx(lf.write_docx(self.doc))
        self.assertEqual(outline(again.blocks), outline(self.blocks))

    def test_round_trip_through_editor_html(self):
        again = lf.from_html(lf.to_html(self.doc))
        self.assertEqual(outline(again.blocks), outline(self.blocks))

    def test_written_xml_is_well_formed(self):
        z = zipfile.ZipFile(io.BytesIO(lf.write_odt(self.doc)))
        for name in ('content.xml', 'styles.xml', 'META-INF/manifest.xml'):
            lf.parse_xml(z.read(name))


class EditorHtmlTest(unittest.TestCase):
    def test_what_webkit_produces(self):
        html = ('<h2>Title</h2><div>loose <b>bold</b><span style="font-weight: bold;">also</span></div>'
                'text outside<p><br></p><ul><li>one</li><li>two<ul><li>deep</li></ul></li></ul>'
                '<p style="text-align: center;"><strike>gone</strike> <a href="javascript:alert(1)">bad</a> '
                '<a href="https://example.org/x">good</a></p><script>alert(1)</script>'
                '<table><tbody><tr><th>H</th><td colspan="2">c</td></tr></tbody></table><hr class="page-break">')
        b = lf.from_html(html).blocks
        self.assertEqual((b[0].style, text_of(b[0])), ('h2', 'Title'))
        self.assertEqual(text_of(b[1]), 'loose boldalso')
        self.assertTrue(all(r.b for r in b[1].runs if r.text in ('bold', 'also')))
        self.assertEqual(text_of(b[2]), 'text outside')
        self.assertEqual(b[3].runs, [])                 # the empty line stays
        self.assertEqual(text_of(b[4].items[1][1].items[0][0]), 'deep')
        p = b[5]
        self.assertEqual(p.align, 'center')
        self.assertTrue(p.runs[0].s)
        hrefs = [r.href for r in p.runs if isinstance(r, Text)]
        self.assertNotIn('javascript:alert(1)', hrefs)
        self.assertIn('https://example.org/x', hrefs)
        self.assertNotIn('alert', ''.join(text_of(x) for x in b if isinstance(x, Para)))
        self.assertTrue(b[6].rows[0][0].blocks[0].runs[0].b)
        self.assertEqual(b[6].rows[0][1].colspan, 2)
        self.assertIsInstance(b[7], PageBreak)

    def test_html_out_is_escaped(self):
        doc = lf.Document([Para([Text('<script>alert("x")</script> & "q"')])])
        html = lf.to_html(doc)
        self.assertNotIn('<script>', html)
        self.assertEqual(text_of(lf.from_html(html).blocks[0]), '<script>alert("x")</script> & "q"')

    def test_only_real_images_survive(self):
        good = 'data:image/png;base64,' + __import__('base64').b64encode(fixtures.png(2, 2, (0, 0, 0))).decode()
        html = f'<p><img src="{good}" width="10"><img src="http://example.org/x.png"><img src="data:image/svg+xml;base64,PHN2Zz4="></p>'
        runs = lf.from_html(html).blocks[0].runs
        self.assertEqual([type(r).__name__ for r in runs], ['Image'])
        self.assertEqual(runs[0].width, 10)


class OtherFormatsTest(unittest.TestCase):
    def test_rtf(self):
        doc = lf.read_rtf(fixtures.SAMPLE_RTF)
        first, second = doc.blocks[0], doc.blocks[1]
        self.assertEqual(first.align, 'center')
        self.assertEqual(text_of(first), 'Hello bold and italic café €.')
        self.assertTrue([r for r in first.runs if r.text == 'bold'][0].b)
        self.assertTrue(any(isinstance(r, Break) for r in second.runs))
        self.assertNotIn('Sample Author', lf.plain_text(doc.blocks))
        self.assertEqual(lf.detect(fixtures.SAMPLE_RTF), 'rtf')

    def test_txt(self):
        doc = lf.read_txt('one\r\ntwo\n\nfour\n'.encode())
        self.assertEqual([text_of(b) for b in doc.blocks], ['one', 'two', '', 'four'])
        self.assertEqual(lf.save(doc, 'txt'), b'one\ntwo\n\nfour')
        self.assertEqual(text_of(lf.read_txt(b'caf\xe9').blocks[0]), 'café')

    def test_detect(self):
        self.assertEqual(lf.detect(fixtures.pages_pdf()), 'pdf')
        self.assertEqual(lf.detect(b'\xd0\xcf\x11\xe0rest'), 'ole')
        self.assertEqual(lf.detect(b'\x00\x01\x02binary'), None)


class HostileFilesTest(unittest.TestCase):
    def test_doctype_refused(self):
        bomb = (b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
                b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                b'<w:p><w:r><w:t>&b;</w:t></w:r></w:p></w:body></w:document>')
        data = lf.make_zip([('word/document.xml', bomb)])
        with self.assertRaises(lf.FormatError):
            lf.read_docx(data)

    def test_zip_bomb_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('word/document.xml', b'\0' * (lf.MAX_MEMBER + 1))
        with self.assertRaises(lf.FormatError):
            lf.read_docx(buf.getvalue())

    def test_external_pictures_not_loaded(self):
        rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                'Target="http://127.0.0.1:9/x.png" TargetMode="External"/></Relationships>')
        body = (f'<w:document {fixtures.W_NS}><w:body><w:p><w:r><w:drawing><wp:inline><wp:extent cx="1" cy="1"/>'
                '<a:graphic><a:graphicData><pic:pic><pic:blipFill><a:blip r:link="r1"/></pic:blipFill></pic:pic>'
                '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p></w:body></w:document>')
        doc = lf.read_docx(lf.make_zip([('word/document.xml', body), ('word/_rels/document.xml.rels', rels)]))
        self.assertIn('internet', ' '.join(doc.warnings))
        self.assertNotIn('127.0.0.1', lf.to_html(doc))

    def test_garbage(self):
        for data in (b'PK\x03\x04garbage', b'', b'{\\rtf1 {{{{'):
            try:
                kind = lf.detect(data)
                if kind:
                    lf.load(data, kind)
            except lf.FormatError:
                pass


if __name__ == '__main__':
    unittest.main()
