# Bundled libraries

Copied unchanged from the npm registry; `SHA256SUMS` lists every file here
(the release workflow checks them).

| Library | Version | Licence | npm integrity (of the package) |
| --- | --- | --- | --- |
| [PDF.js](https://github.com/mozilla/pdf.js) (`pdfjs-dist`) | 6.4.299 | Apache 2.0 (`pdfjs/LICENSE`) | `sha512-AVl138zALtfaAPvADulE0PZThbYzCBS79nL4pOSL/6Sm/4AH5A21BD9VHt97OlCuzJuCpmeZtAtkinisF4Vb1g==` |
| [pdf-lib](https://github.com/Hopding/pdf-lib) | 1.17.1 | MIT (`pdf-lib.LICENSE.md`) | `sha512-V/mpyJAoTsN4cnP31vc0wfNA1+p20evqqnap0KLoRUN0Yk/p3wN52DOEsL4oBFcLdb76hlpKPtzJIgo67j/XLw==` |

From `pdfjs-dist` only these are kept: `build/pdf.min.mjs`, `build/pdf.worker.min.mjs`,
`web/pdf_viewer.mjs`, `web/pdf_viewer.css`, `web/images/`, `cmaps/`, `standard_fonts/`,
`iccs/`, and the JPEG 2000 / JBIG2 / colour-profile decoders from `wasm/` (with their licences).

Left out on purpose: `build/pdf.sandbox*` and `wasm/quickjs-eval.*`, the JavaScript
engine PDF.js would use to run a PDF's own scripts. Without them, a PDF's
JavaScript can't run in Leaflet at all.

From `pdf-lib` only `dist/pdf-lib.min.js` (the self-contained build) is kept.

To update: download the new package with `npm pack`, check its integrity, copy the
same files, then `cd ui/vendor && find . -type f ! -name SHA256SUMS ! -name README.md | sort | xargs sha256sum > SHA256SUMS`.
