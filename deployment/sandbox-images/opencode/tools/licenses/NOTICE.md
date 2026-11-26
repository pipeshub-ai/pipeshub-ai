# Third-party notices: PipesHub opencode tools image (`pxh-opencode-tools:1.18.34`)

This image is the opencode engine image (`pxh-opencode:1.18.34`; its notices are in
`/usr/share/licenses/pxh-opencode/`) built on CPython 3.12 for Alpine, with the Python packages listed
below installed unmodified from their published wheels, and Node.js with the npm packages below installed
unmodified from the npm registry. `components.json` in this folder names every component this image adds, with
its version and licence; each wheel's own licence files stay in
`/usr/local/lib/python3.12/site-packages/<package>.dist-info/`, each npm package's in
`/usr/local/lib/node_modules/<package>/`.

## CPython

CPython 3.12.15 (`python:3.12-alpine3.24`), Python Software Foundation License Version 2
(`/usr/local/lib/python3.12/LICENSE.txt`). The Alpine packages it depends on (libffi, sqlite-libs, libbz2,
xz-libs, gdbm, krb5-libs, libtirpc, libnsl, libuuid and others listed in `components.json`) are unmodified
distribution packages, aggregated in the image under their own licences.

## Python packages

| Package | Version | Licence |
|---|---|---|
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| pandas | 3.0.6 | BSD-3-Clause |
| matplotlib | 3.11.2 | Matplotlib License (PSF-based) |
| seaborn | 0.13.2 | BSD-3-Clause |
| openpyxl | 3.1.5 | MIT |
| XlsxWriter | 3.2.9 | BSD-2-Clause |
| python-docx | 1.2.0 | MIT |
| python-pptx | 1.0.2 | MIT |
| scipy | 1.18.1 | BSD-3-Clause |
| reportlab | 4.5.1 | BSD-3-Clause (its GPL DarkGarden font and unlicensed Type 1 fonts removed; Bitstream Vera fonts kept) |
| pypdf | 6.19.0 | BSD-3-Clause |
| pdfplumber | 0.11.10 | MIT |
| pdfminer.six | 20260107 | MIT |
| pypdfium2 | 5.14.0 | Apache-2.0 OR BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| cffi | 2.1.1 | MIT-0 |
| pycparser | 3.12 | BSD-3-Clause |
| charset-normalizer | 3.5.2 | MIT |
| tabulate | 0.10.0 | MIT |
| Jinja2 | 3.1.6 | BSD-3-Clause |
| contourpy | 1.4.0 | BSD-3-Clause |
| cycler | 0.12.1 | BSD-3-Clause |
| et-xmlfile | 2.0.0 | MIT |
| fonttools | 4.66.1 | MIT |
| kiwisolver | 1.5.1 | BSD-3-Clause |
| lxml | 6.1.3 | BSD-3-Clause |
| MarkupSafe | 3.0.4 | BSD-3-Clause |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pillow | 12.3.0 | MIT-CMU (HPND) |
| pyparsing | 3.3.3 | MIT |
| python-dateutil | 2.9.0.post0 | Apache-2.0 AND BSD-3-Clause |
| six | 1.17.0 | MIT |
| typing-extensions | 4.16.0 | PSF-2.0 |

## Native libraries inside the wheels

The wheels carry shared libraries in `<package>.libs/` (and lxml links some statically):

| Library | Where | Licence |
|---|---|---|
| libgcc_s, libstdc++ | contourpy, kiwisolver, matplotlib, numpy, pandas | GPL-3.0-or-later WITH GCC-exception-3.1 |
| libgfortran | numpy (OpenBLAS's Fortran runtime) | GPL-3.0-or-later WITH GCC-exception-3.1 |
| libquadmath | numpy | LGPL-2.1-or-later |
| scipy-openblas64 | numpy | BSD-3-Clause |
| libgcc_s, libstdc++, libgfortran | scipy | GPL-3.0-or-later WITH GCC-exception-3.1 |
| libquadmath | scipy | LGPL-2.1-or-later |
| scipy-openblas (OpenBLAS with LAPACK) | scipy | BSD-3-Clause (LAPACK: BSD-3-Clause-Open-MPI) |
| libgcc_s | cryptography, pypdfium2 | GPL-3.0-or-later WITH GCC-exception-3.1 |
| libstdc++ | pypdfium2 | GPL-3.0-or-later WITH GCC-exception-3.1 |
| OpenSSL (static) | cryptography | Apache-2.0 |
| PDFium (static) with FreeType, libjpeg-turbo, libpng, libtiff, lcms2, OpenJPEG, zlib, AGG 2.3, abseil, fast_float, ICU, musl | pypdfium2 | PDFium BSD-3-Clause; the others their own (FreeType: FTL only; AGG: its permission notice; ICU: Unicode-3.0), texts in `pypdfium2-*.dist-info/licenses/BUILD_LICENSES/` |
| Bitstream Vera fonts | reportlab | Bitstream-Vera (`reportlab/fonts/bitstream-vera-license.txt`) |
| libjpeg-turbo, libpng, libtiff, libwebp, libavif, FreeType, HarfBuzz, lcms2, OpenJPEG, Brotli, zstd, liblzma, libxcb, libXau, libXdmcp, libbsd, libmd | pillow | their own licences (see `components.json`; FreeType is FTL OR GPL-2.0-or-later) |
| libxml2, libxslt, libiconv | lxml (static) | MIT, MIT, LGPL-2.1-or-later |

The GCC runtime library exception permits linking libgcc_s, libstdc++ and libgfortran into any program.
For the LGPL libraries (libquadmath, libiconv), PipesHub does not modify them; their sources are at
<https://gcc.gnu.org/> and <https://www.gnu.org/software/libiconv/>, and the wheels' build recipes at
<https://github.com/numpy/numpy>, <https://github.com/scipy/scipy> and <https://github.com/lxml/lxml>.

## Node.js and npm packages

Node.js 24.22.1 (Alpine's `nodejs` package, MIT; on Claude Code's Debian image the official glibc build of the
same release, its licence in `/usr/share/licenses/nodejs/LICENSE`), with the Alpine packages it depends on
(icu-libs and icu-data-en under the Unicode License V3, ada-libs under (Apache-2.0 OR MIT) AND MPL-2.0, simdjson
and simdutf under Apache-2.0 OR MIT). npm itself is not installed.

| Package | Version | Licence |
|---|---|---|
| docx | 9.7.1 | MIT |
| exceljs | 4.4.0 | MIT |
| pptxgenjs | 4.0.1 | MIT |
| pdfkit | 0.20.2 | MIT |
| pdf-lib | 1.17.1 | MIT |

Their dependencies (`node/package-lock.json`, every one in `components.json`) are under MIT, ISC, BSD-2-Clause,
BSD-3-Clause, Apache-2.0, BlueOak-1.0.0 or Unlicense, with these exceptions: jszip is dual licensed MIT OR
GPL-3.0-or-later and is used under MIT; pako is MIT AND Zlib; buffers 0.1.1 (through exceljs's unzipper)
declares no licence (approved as found, `scripts/engines/licence_allowlist.json`); png-js 2.0.0 names no licence in
its package.json and ships the MIT License. pdfkit carries Adobe's metrics of the 14 standard PDF fonts
(`pdfkit/js/data/*.afm`, APAFML).

Whether shipping this image is distribution under these licences, and the D29 decision on weak-copyleft
components, are for legal sign-off (PROJECT-PLAN D29), as for the engine image.
