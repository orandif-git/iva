# Jas iVision brand files

`make_logo.py` builds every logo file from one source into `backend/web/img/` (and `backend/web/favicon.ico`):

    pip install fonttools
    python3 brand/make_logo.py

- **Mark**: an eye whose iris is the JAS globe, framed by orbit swoosh lids (as in the JAS Infotech logo). Brand blue `#128BE0`, deep blue `#0A3D73`.
- **Lockup**: the mark, `JAS` in heavy italic sans (brand blue), `iVision` in bold italic serif, and the tagline *Intelligent Vision. Automated Insights.*
- **Files**: `logo.svg` follows the visitor's light or dark setting; `logo-on-light.*` and `logo-on-dark.*` are fixed; `logo-mark.svg` is the mark alone;
  `favicon.svg`, `favicon.ico`, `favicon-16/32/48.png`, `apple-touch-icon.png`, `icon-192/512.png` and `site.webmanifest` are for browsers and phones.
- Lettering is outlines, so nothing depends on installed fonts. The fonts used (Inter and Liberation Serif) are open-licensed.
- To change colours, text or shapes, edit `make_logo.py` and run it again. Replace everything by hand-made files if a designer supplies a final logo: keep the same file names.
