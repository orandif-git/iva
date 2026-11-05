# Jas iVision brand files

Everything is built from one source, so every size and variant matches:

    pip install fonttools pillow        # and Chromium through Playwright (Node) for the PNG files
    python3 brand/make_logo.py          # writes backend/web/img/ and backend/web/favicon.ico

## The design

- **Mark** (`jas_mark.py`): the JAS Infotech globe inside an AI detection frame (corner brackets). The globe is **traced from the brand's own logo file**
  (`jas-infotech-logo.png`, by `trace_globe.py` into `jas_globe_path.txt`), smoothed and matched to the original stroke weight, so it is the same globe as a
  clean vector. AI: a sparkle in the globe's orbit gap and network nodes where its lines cross.
- **Name**: `JAS` in heavy italic sans (brand blue) over `iVision` in bold italic serif, as in the JAS Infotech logo. The **"o" of iVision is a camera lens**
  whose pupil is the AI sparkle: the camera eye is part of the name. The left edges of J, i and I are aligned on their ink, and the mark is centred on the text block.
- **Tagline**: *Intelligent Vision. Automated Insights.*
- **Colours**: brand blue `#128BE0` (sampled from the JAS logo), deep blue `#0A3D73`, AI cyan `#35C6F4`, ink `#0B1220`, grey `#5B6B7A`, dark background `#0C1620`.
- Lettering is outlines, so nothing depends on installed fonts (Inter and Liberation Serif, both open-licensed).

## Which file to use

| Need | File |
|---|---|
| Website, app (follows light or dark mode by itself) | `logo.svg` (full), `logo-compact.svg` (headers, no tagline), `logo-stacked.svg` (square-ish spaces) |
| On a fixed light or dark background | `logo-on-light.svg` / `logo-on-dark.svg` (and `-compact-`, `-stacked-` versions) |
| Documents, slides, email (pictures, 4x size) | `logo-transparent.png`, `logo-transparent-dark.png`, `logo-on-light.png`, `logo-on-dark.png`, `logo-compact.png`, `logo-stacked.png` |
| One colour: stamps, fax, embossing, engraving, single-colour print | `logo-black.svg/png`, `logo-white.svg/png`, `logo-mark-black.svg`, `logo-mark-white.svg` |
| Profile picture, small square | `logo-mark.svg`, `logo-mark-512.png`, `icon-512.png` |
| Browser tab, phone home screen | `favicon.svg`, `favicon.ico` (16, 32, 48 px, each hand-tuned), `apple-touch-icon.png`, `icon-192.png`, `icon-512.png`, `icon-maskable-512.png` (Android circle-safe), `site.webmanifest` |
| Link previews, social media | `og-image.png` (1200 x 630) |

## Using it well

- **Clear space**: keep free space around the logo equal to the width of one frame bracket (about a quarter of the mark's width).
- **Smallest sizes**: full logo 160 px wide (35 mm in print); compact 110 px; mark alone 24 px. Below that use the favicon.
- **Do not**: stretch or rotate it, change its colours, add shadows or outlines, place it on a busy picture, or put the blue lettering on a blue background (use the white or dark version).
- At 16 px the favicon drops the globe (too fine to read) and keeps the frame and the AI sparkle.

## Changing it

- Edit `make_logo.py` (layout, variants, colours) or `jas_mark.py` (the mark) and run it again.
- To re-trace the globe from a new version of the JAS logo: `pip install potracer numpy`, then `python3 brand/trace_globe.py brand/jas-infotech-logo.png`.
- If a designer supplies a final logo, replace the files in `backend/web/img/` and keep the same names.
