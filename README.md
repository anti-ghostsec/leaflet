# Leaflet

A light, private app for PDFs and word-processor documents on Linux.

Leaflet is for the everyday jobs that don't need an office suite: read a PDF, fill in a form,
highlight something, put pages in order, join two PDFs; open a `.docx` or `.odt`, fix a few
lines, and save it again or send it on as a PDF. It is a small GTK 4 app around a WebKit view,
starts in a moment, and runs comfortably on a 1 GB machine.

## Features

**PDF**
- Fast viewing with a page sidebar, zoom (fit width, fit page, 25–500 %), page jump, find with match count
- Select and copy text
- Fill in forms (text fields, check boxes, radio buttons, lists)
- Highlight text, add notes (text boxes) and freehand drawings
- Turn, delete, reorder (drag in the sidebar, or Alt+↑/↓) and insert pages from another PDF; undo for page changes
- Save, or save a copy; print
- Password-protected PDFs open (viewing, forms and annotations; page changes, and cleaning the metadata on saving,
  need an unencrypted copy)

**Documents**
- Opens DOCX, ODT, RTF and plain text; new documents too
- Text with bold, italic, underline, strike-through, super- and subscript; headings; alignment
- Bulleted and numbered lists (nested), simple tables (add/remove rows and columns), pictures, links, page breaks
- Saves as DOCX, ODT or plain text, and exports as PDF
- An outline of the headings in the sidebar, word count, find

**General**
- Three looks: Light, Dark and Classic (grey and bevelled, made to sit on a Windows 95-style desktop such as FerroOS).
  "Follow the system" picks Light or Dark; `LEAFLET_THEME=classic` forces one.
- Drag a file onto the window to open it; one document per window

## Privacy and safety

- **No network.** The window is served from a private `leaflet://` scheme, its Content-Security-Policy allows
  nothing else, and its web view's proxy is a closed port, so a document can't load anything from the internet,
  not even a picture. Links in documents open in your browser only when you click one and say yes.
- **A PDF's JavaScript never runs.** PDF.js is bundled without its scripting support and without the JavaScript
  engine it would use (`quickjs`); there's nothing in Leaflet that could run a PDF's scripts. Launch actions are
  ignored.
- **Nothing added to the files you save.**
  - Saved PDFs: the author, creator, producer, keywords, subject and dates in the document info are removed, and so
    are the XMP metadata packets (the document's, and those on its pages and pictures). The title is kept, because
    viewers show it. Notes and other annotations lose the name of whoever made them and their dates; what they say
    stays. Pictures stored as JPEG lose their EXIF (camera, place, time), XMP, IPTC, thumbnails and comments.
  - Pages you deleted, and old versions of changed parts, are removed from the file rather than left hidden in it,
    and that includes what pointed at a deleted page: its bookmarks, links to it, its form fields and their values,
    and its entries in the tag (structure) tree.
  - Exported PDFs (from a document): the same; there's no "created with" or creation date.
  - DOCX and ODT: written without a metadata part (`docProps/core.xml`, `meta.xml`), so no author, company,
    editing time or program name; every file inside the zip has the same fixed date. Author names and the like that
    were in the original file are not carried over either. Pictures are written without their EXIF (GPS position,
    camera, time), XMP, IPTC, comments and text; the picture itself is copied as it is, not re-compressed.
  - What a picture keeps is what it needs to look the same: its colour profile (ICC) and colour markers, and the one
    EXIF field that says which way up it is.
  - An encrypted PDF is the exception: Leaflet can't rewrite it, so it is saved with its metadata as it was, and
    Leaflet says so before saving.
- **No list of recent files** unless you switch it on in Settings. Opened files aren't added to the desktop's
  "Recent files" either.
- **No telemetry, no update checks.** The only file Leaflet writes on its own is `~/.config/leaflet/settings.json`
  (theme, window size, and the recent list if you switched it on). Printing a PDF and exporting one go through a
  private temporary folder, which is removed as soon as the job is done, and when Leaflet quits or is stopped.
- **Untrusted files are handled with care.** Word and OpenDocument files are read with Python's standard library:
  sizes are checked before anything is unpacked, XML with a DOCTYPE is refused by the parser itself, whatever the
  file's encoding (no entity tricks), and the editor only ever gets HTML that Leaflet built itself from known
  pieces. Pasted content is cleaned the same way. PDFs are parsed by PDF.js inside WebKit's sandboxed web process.
- **The document view can't choose files.** The part of Leaflet that shows a document (and so runs on whatever is
  in it) never names a file to read or write. A file is opened because you picked it in the file dialog, named it
  on the command line, dropped it on the window or clicked it in the recent list; it is saved to the file that is
  open, or where you say in the "Save as" dialog. Nothing else is accepted.

## What Leaflet doesn't do (yet)

Leaflet keeps documents simple, and is honest about it: when a file has something it can't keep, a bar at the top
says what, before you save over the original.

- Documents lose on saving: headers and footers, footnotes, comments, tracked changes (shown as accepted), text
  boxes and shapes, charts, equations, fonts, colours and sizes of text, line spacing, columns, page size and
  margins (saved as A4 with standard margins), cell borders and shading, rows merged across cells (split again).
  Pictures in EMF/WMF/SVG aren't shown. Old binary `.doc` files can't be opened.
- RTF files can be opened and edited, but are saved as DOCX, ODT or text.
- PDFs: no editing of the existing text, no signing (a signed PDF's signature stops being valid when it's saved),
  no XFA forms, no OCR. Highlights are made from selected text; note and drawing colours use PDF.js's defaults.
  Fields and annotations on pages merged in from another PDF come along, but bookmarks (outlines) of the merged
  file don't.
- Saving a PDF removes the names on its notes and comments (see above), which a review that depends on who said
  what won't want. Metadata inside JPEG 2000 pictures in a PDF is not removed, and neither is anything in an
  encrypted PDF.
- A file dropped from a program that hands over only its contents (not a file on disk) opens as a copy: "Save" asks
  where to put it.
- One document per window, no tabs.

## Requirements

- Python 3.10+
- GTK 4 and WebKitGTK 6.0 with GObject introspection

Nothing else: PDF.js and pdf-lib are included (see [ui/vendor](ui/vendor/README.md)).

Debian / Ubuntu:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-webkit-6.0
```

Fedora:

```bash
sudo dnf install python3-gobject gtk4 webkitgtk6.0
```

Arch:

```bash
sudo pacman -S python-gobject gtk4 webkitgtk-6.0
```

## Run

```bash
python3 leaflet.py [FILE ...]
```

To add Leaflet to your app menu (and the "Open with" list for PDF, DOCX, ODT and RTF files):

```bash
./install.sh
```

`./install.sh --uninstall` removes it again.

## Keyboard shortcuts

| Action | Keys |
| --- | --- |
| Open / New document / Save / Save as | Ctrl+O / Ctrl+N / Ctrl+S / Ctrl+Shift+S |
| Print / Close window / Quit | Ctrl+P / Ctrl+W / Ctrl+Q |
| Find / next / previous | Ctrl+F / F3 or Enter / Shift+F3 or Shift+Enter |
| Zoom in / out / fit width | Ctrl+= / Ctrl+− / Ctrl+0, or Ctrl+wheel |
| Pages or outline sidebar / Full screen / Shortcuts | F9 / F11 / F1 |
| PDF: go to page / first / last page | Ctrl+G / Home / End |
| PDF: turn pages right / left | Ctrl+R / Ctrl+Shift+R |
| PDF: select, highlight, note, draw | Esc, H, N, D |
| PDF page list: select more / all, delete, move | Shift- or Ctrl+click / Ctrl+A, Delete, Alt+↑/↓ or drag |
| Document: bold / italic / underline | Ctrl+B / Ctrl+I / Ctrl+U |
| Document: text / heading 1–3 | Ctrl+0 / Ctrl+1–3 |
| Document: bullets / numbering, indent in a list | Ctrl+Shift+8 / Ctrl+Shift+7, Tab / Shift+Tab |
| Document: align left / centre / right / justify | Ctrl+L / Ctrl+E / Ctrl+R / Ctrl+J |
| Document: page break / export as PDF | Ctrl+Enter / Ctrl+Shift+E |

## Tests

```bash
tests/run.sh           # document formats, what the window accepts from the page (Python), PDF operations (node)
tests/run.sh --live    # also drives the real window on a virtual screen (Xvfb), with screenshots
```

The test files are made from scratch by `tests/fixtures.py`, including a PDF that tries to run JavaScript and
reach a local address: the live check counts every request the window makes, and fails on any. The PDF tests open
what they save with the bundled PDF.js too, and with `qpdf --check` when qpdf is installed.

## License

Code is MIT licensed (see [LICENSE](LICENSE)). PDF.js is under the Apache License 2.0 and pdf-lib under the MIT
License; see [ui/vendor](ui/vendor/README.md).
