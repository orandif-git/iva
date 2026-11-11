# Jas iVision design system

This is the approved look and feel of the customer app and the staff console (approved 11 November 2026). **Every new screen and every change follows it.** The approved prototype
is kept in `docs/design/` (`Main`, `Live`, `Evidence`, `Setup`) and the working version of every rule below is in the app's code:

| What | Where |
|---|---|
| Colours, corner sizes, motion timings, heading style | `app/lib/theme.dart` (`Tok`, `Radii`, `Motion`, `display()`) |
| Motion and loading | `app/lib/widgets/motion.dart` (`RiseIn`, `Skeleton`, `SkeletonPage`, `SkeletonRows`, `Pressable`, `Grow`) |
| Shared pieces | `app/lib/screens/common.dart` (`PageTitle`, `StatusPill`, `StatStrip`, `Segments`, `Group`, `GroupLabel`, `Section`, `Pill`, `Badge2`, `EmptyState`) |
| Tab bar and top bar | `app/lib/widgets/glass_nav.dart`, `app/lib/main.dart` (`Shell`) |
| Staff console (`/staff`) | the "design system" block at the end of `backend/web/css/app.css`: the same tokens as CSS variables, fonts in `backend/web/css/fonts/` |

Use these pieces. Do not write a colour, a corner size or an animation by hand in a screen: if something is missing, add it to the shared files first.

## Colour (light; dark has a matching value for each in `Tok.dark`)

| Token | Value | Meaning |
|---|---|---|
| `bg` | `#F4F6FA` | the page |
| `surface` | `#FFFFFF` | cards, bars |
| `surface2` | `#E9EDF4` | the track of a segment bar, quiet fills |
| `line` | `#E3E8F0` | hairlines and card borders |
| `fg` / `fg2` | `#0E1726` / `#55627A` | text / secondary text |
| `brand` / `brandSoft` / `brandDeep` | `#0B6FB8` / `#E6F1FB` / `#0A3D73` | what you can act on |
| `okInk` / `okSoft` | `#0B6B3A` / `#DDF4E6` | working, fine |
| `warnInk` / `warnSoft` | `#7A4700` / `#FFF0D1` | look soon |
| `crit` / `critSoft` | `#B42318` / `#FDE7E4` | fix now, needs you |
| `ai` / `aiSoft` | `#5B45D6` / `#EEEAFE` | the AI is involved (Smart Alerts) |
| picture ground | `#1B2A41` | behind a camera picture before it arrives |

Colour always means the same thing. Never use red, green, amber or violet as decoration.

## Type

- **Headings and big numbers:** Space Grotesk Bold, through `display(size)`. Screen title 26, card figures 24–28, bar titles 18–19.
- **Everything else:** Inter. Row title 15–16 semi-bold, body 14–15, secondary 12.5–13.5 in `fg2`.
- One screen title per screen (`PageTitle`). Words are plain: say what happened, never a setting name.

## Shape and space

- Corners: card 22, tile 18, button 16, small control 14, picture in a card 14, tab bar and pills fully round (`Radii`).
- Cards are white with a 1px `line` border and **no shadow**. Only the floating tab bar has a shadow.
- 16 from the screen edge on a phone (32 on a wide screen), 14 between cards, 12 between tiles.
- Anything you can press is at least 44 high. Main buttons are 50 high; one filled (blue) button per group, the other outlined.
- Rows in a list live in one `Group` with hairlines between them, each with a 40 rounded-square icon tinted by its meaning.

## Layout

- **Phone:** top bar (mark, "Jas iVision", business name, bell, profile) · the page · floating tab bar with five places: Home, Cameras, AI Watch, Activity, More.
- **Wide (900 and up):** the same five places in a left rail, the title in the page header.
- **Home:** greeting → one status pill → "Needs you" (the newest alert large, with its picture and Done / Snooze 1 hour) → today's figures (`StatStrip`) →
  a strip of cameras → system health.
- A choice between a few things is a `Segments` bar. A filter is a pull-down pill (`FilterMenu`).
- A sub-screen opens over the app with a back arrow and its own title, and one clear action pinned where the thumb is.
- Leave `navClear` of room at the bottom of every scrolling page so the tab bar never covers the last row.

## Motion

- A screen **rises in** (fade and 14px up, 320ms) when it opens or the tab changes: `RiseIn`.
- Sub-screens **slide in from the right** and can be dragged back.
- While a screen loads it shows its **shimmering outline** (`SkeletonPage`, or `SkeletonRows` for part of a page). No spinners. The animated logo is only for
  starting the app.
- Pressable things give a little (`Pressable`, 0.97). Switches and the tab highlight glide. Chart bars grow from the floor (`Grow`). A new count pops in.
- **All of it stops** when the device asks for reduced motion (`reduceMotion`). Motion never delays the person: nothing waits for an animation to finish.

## Before you finish a change

1. Only shared tokens and pieces were used (search your change for `Color(0x` and `BorderRadius.circular(` with a bare number).
2. It works at 390 wide and at 1200 wide, in light and dark.
3. Loading shows a skeleton, an error says what to do, an empty state says what will appear.
4. Buttons are real buttons with a spoken label; nothing is smaller than 44.
5. `flutter analyze` and `flutter test` pass.
