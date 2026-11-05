# Jas iVision brand files

`make_logo.py` builds every logo file from one source into `backend/web/img/` (and `backend/web/favicon.ico`):

    pip install fonttools pillow
    python3 brand/make_logo.py

- **Mark** (`jas_mark.py`): the JAS Infotech globe, inside an AI detection frame (corner brackets). The globe is **traced from the brand's own logo file**
  (`jas-infotech-logo.png`, by `trace_globe.py` into `jas_globe_path.txt`), so it is the same globe, redrawn as clean vector curves.
  AI elements: a sparkle in the globe's orbit gap, a small second sparkle, and network nodes on the points where the globe's lines cross.
  Brand blue `#128BE0` (sampled from the JAS logo), deep blue `#0A3D73`, AI cyan `#35C6F4`.
- **Lockup**: the mark, `Jas` in heavy italic sans (brand blue), `iVision` in bold italic serif (as in the JAS logo's "Infotech"), and the tagline
  *Intelligent Vision. Automated Insights.*
- **Files**: `logo.svg` follows the visitor's light or dark setting; `logo-on-light.*` and `logo-on-dark.*` are fixed; `logo-mark.svg` is the mark alone;
  `favicon.svg`, `favicon.ico`, `favicon-16/32/48.png` (simplified, no nodes), `apple-touch-icon.png`, `icon-192/512.png` and `site.webmanifest` are for browsers and phones.
- Lettering is outlines, so nothing depends on installed fonts. The fonts used (Inter and Liberation Serif) are open-licensed.
- To re-trace the globe from a new version of the JAS logo: `pip install potracer numpy` then `python3 brand/trace_globe.py brand/jas-infotech-logo.png`.
- To change colours, text or placement, edit `make_logo.py` or `jas_mark.py` and run it again. If a designer supplies a final logo, replace the files in
  `backend/web/img/` and keep the same names.
