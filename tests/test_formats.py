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

    def test_doctype_refused_in_any_encoding(self):
        # UTF-16 hides "<!DOCTYPE" from a search through the bytes.
        xml = ('<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "expanded-entity">]>'
               '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
               '<w:p><w:r><w:t>&a;</w:t></w:r></w:p></w:body></w:document>')
        for codec, bom in (('utf-16-le', b'\xff\xfe'), ('utf-16-be', b'\xfe\xff'), ('utf-16-le', b''),
                           ('utf-16-be', b''), ('utf-32-le', b'\xff\xfe\0\0'), ('utf-32-be', b'\0\0\xfe\xff'),
                           ('utf-8', b'\xef\xbb\xbf'), ('latin-1', b'')):
            with self.subTest(codec=codec, bom=bool(bom)):
                body = bom + xml.encode(codec)
                with self.assertRaises(lf.FormatError):
                    lf.parse_xml(body)
                with self.assertRaises(lf.FormatError):
                    lf.read_docx(lf.make_zip([('word/document.xml', body)]))
        declared = xml.replace('?>', ' encoding="UTF-16"?>', 1)
        with self.assertRaises(lf.FormatError):
            lf.parse_xml(b'\xff\xfe' + declared.encode('utf-16-le'))

    def test_utf16_xml_without_doctype_is_read(self):
        xml = ('<?xml version="1.0" encoding="UTF-16"?>'
               '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
               '<w:p><w:r><w:t>caf\u00e9 &amp; t\u00e9</w:t></w:r></w:p></w:body></w:document>')
        for codec, bom in (('utf-16-le', b'\xff\xfe'), ('utf-16-be', b'\xfe\xff')):
            doc = lf.read_docx(lf.make_zip([('word/document.xml', bom + xml.encode(codec))]))
            self.assertEqual(text_of(doc.blocks[0]), 'caf\u00e9 & t\u00e9')

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


def members(data):
    """Every file inside a saved document, unpacked."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {name: z.read(name) for name in z.namelist()}


class UnpackedTotalTest(unittest.TestCase):
    """What is really unpacked counts, not what the zip's headers claim."""

    def test_running_total(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            for n in 'ab':
                z.writestr(n, b'x' * 40)
        saved = lf.MAX_MEMBER, lf.MAX_TOTAL
        lf.MAX_MEMBER, lf.MAX_TOTAL = 60, 100
        try:
            # The headers' 80 bytes pass; reading 'a' a second time makes
            # 120 really unpacked, which doesn't.
            z = lf.open_zip(buf.getvalue())
            lf.read_member(z, 'a')
            lf.read_member(z, 'b')
            with self.assertRaises(lf.FormatError):
                lf.read_member(z, 'a')
        finally:
            lf.MAX_MEMBER, lf.MAX_TOTAL = saved


class PictureMetadataTest(unittest.TestCase):
    """Pictures go into saved documents without EXIF (GPS, camera), XMP, IPTC, comments, text or dates."""

    PHOTOS = (('image/jpeg', fixtures.photo_jpeg, fixtures.plain_jpeg), ('image/png', fixtures.photo_png, fixtures.plain_png),
              ('image/gif', fixtures.photo_gif, fixtures.plain_gif))

    def test_fixtures_carry_metadata(self):
        for _mime, photo, _plain in self.PHOTOS:
            self.assertTrue(any(mark in photo() for mark in fixtures.PHOTO_MARKS))

    def test_only_metadata_is_removed(self):
        # The result is the same file, byte for byte, as one made without the
        # metadata: the picture data isn't re-encoded, the colour profile, JFIF
        # and Adobe markers and the PNG colour chunks stay.
        for mime, photo, plain in self.PHOTOS:
            with self.subTest(mime=mime):
                out = lf.strip_image_metadata(photo(), mime)
                self.assertEqual(out, plain())
                for mark in fixtures.PHOTO_MARKS:
                    self.assertNotIn(mark, out)
                self.assertEqual(lf.strip_image_metadata(out, mime), out)
                self.assertEqual(lf.image_size(out), lf.image_size(photo()))
        self.assertIn(b'ICC_PROFILE\x00\x01\x01sample colour profile', fixtures.plain_jpeg())

    def test_orientation_is_the_one_exif_field_kept(self):
        def orientation(data):
            at = data.find(b'Exif\x00\x00')
            if at >= 0:
                return lf._exif_orientation(data[at + 6:])
            at = data.find(b'eXIf')
            return lf._exif_orientation(data[at + 4:]) if at >= 0 else 1
        for n in range(1, 9):
            for photo, plain in ((fixtures.photo_jpeg, fixtures.plain_jpeg), (fixtures.photo_png, fixtures.plain_png)):
                out = lf.strip_image_metadata(photo(n))
                self.assertEqual(orientation(photo(n)), n)
                self.assertEqual(orientation(out), n)
                self.assertEqual(out, plain(n))
                self.assertEqual(b'Exif' in out or b'eXIf' in out, n != 1)       # upright needs no EXIF at all
        big_endian = fixtures.png(24, 12, (30, 120, 60), fixtures.PNG_COLOUR, fixtures.png_chunk(b'eXIf', fixtures.exif(8, '>')))
        self.assertEqual(lf.strip_image_metadata(big_endian), fixtures.plain_png(8))

    def test_damaged_pictures(self):
        photo = fixtures.photo_jpeg()
        cut = photo[:photo.rindex(b'\xff\xd9') - 1]              # ends in the middle of the picture data
        out = lf.strip_image_metadata(cut)
        self.assertEqual(out, fixtures.plain_jpeg()[:-3])
        for junk in (b'', b'\xff\xd8', b'\xff\xd8\xff\xe1\xff\xff', photo[:40], fixtures.photo_png()[:60],
                     fixtures.photo_gif()[:20], b'\x89PNG\r\n\x1a\n' + b'\xff' * 30, b'BM' + b'\0' * 40):
            self.assertEqual(lf.strip_image_metadata(junk), junk)     # left as it was, and no error

    def document(self):
        return lf.Document([Para([Text('Three pictures: ')] + [Image(photo(), mime, 16, 8, 'a picture')
                                                               for mime, photo, _plain in self.PHOTOS])])

    def check_saved(self, data, read):
        for name, body in members(data).items():
            for mark in fixtures.PHOTO_MARKS:
                self.assertNotIn(mark, body, f'{mark!r} in {name}')
        images = [r for r in read(data).blocks[0].runs if isinstance(r, Image)]
        self.assertEqual([im.data for im in images], [plain() for _mime, _photo, plain in self.PHOTOS])

    def test_saved_docx_and_odt(self):
        self.check_saved(lf.write_docx(self.document()), lf.read_docx)
        self.check_saved(lf.write_odt(self.document()), lf.read_odt)

    def test_saved_from_the_editor(self):
        # the way a picture comes back from the page: as a data: URL in its HTML
        doc = lf.from_html(lf.to_html(self.document()))
        self.assertTrue(any(mark in r.data for r in doc.blocks[0].runs if isinstance(r, Image)
                            for mark in fixtures.PHOTO_MARKS))
        self.check_saved(lf.save(doc, 'docx'), lf.read_docx)
        self.check_saved(lf.save(doc, 'odt'), lf.read_odt)

    def test_opened_and_saved_again(self):
        with_photo = lf.make_zip([(name, fixtures.photo_png() if name == 'word/media/image1.png' else body)
                                  for name, body in members(fixtures.sample_docx()).items()])
        self.assertIn(b'Sample Author', members(with_photo)['word/media/image1.png'])
        saved = lf.write_docx(lf.read_docx(with_photo))
        media = [body for name, body in members(saved).items() if name.startswith('word/media/')]
        self.assertEqual(media, [fixtures.plain_png()])


if __name__ == '__main__':
    unittest.main()
