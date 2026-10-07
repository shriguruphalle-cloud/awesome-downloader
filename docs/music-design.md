# The Music tab's design language

How the Music tab looks and why. The code holds it in two places:
`ui_qt/music_look.py` (colours, type, painting) and `ui_qt/music_widgets.py`
(the parts). Read this before changing either.

## What it learned from

Two reference designs set the direction.

**Artist hero pages** (a "Sound Pulse" website concept). Each artist gets a
full-screen page tinted the colour of their photo: purple for one, burnt
orange for the next, ink blue for a third. Things to keep from it:

- The page colour comes from the picture, so the picture and the page read as
  one image. The photo sits on the right and melts into the colour; it has no
  frame.
- The name is set huge, upper case, in a heavy geometric sans, on the left.
- Three of the artist's songs sit in small columns under the name: TITLE in
  bold caps, then the album, then the year. A thin "All tracks" frame follows.
- Film grain, dust and soft vertical light leaks make a flat gradient read as
  a printed poster.

**A streaming-app layout** (a TIDAL redesign concept). It sets the structure:

- A dark, quiet rail on the left. Discovery comes first, then *Your music*
  (Songs, Artists, Albums), then a list of starting points. The labels are text
  only, in small tracked caps, with no icons.
- The main area is again painted in the picture's colour. Popular songs sit in
  numbered rows (number, +, title over artist, length, ⋮), and albums sit in a
  grid of square covers with a year under each.
- A player bar runs along the bottom across the whole window: cover, title and
  artist on the left, the controls in the middle (the play button a thin
  ring), then time, queue and volume. The song's progress is a hairline along
  the bar's top edge.

## Principles

1. **The page wears its picture.** Every page is painted from one picture:
   the album's cover, the artist's photo, or, for pages without one, the
   song that's playing. With no picture at all, the app's own night navy is
   used. When the picture changes the page cross-fades (about 350 ms); it
   never jumps.
2. **Text is always white, and always readable.** Palette colours are
   darkened until white reads on them: at least 4.5:1 on the lit colour and at
   least 7:1 on the deep one (`make_palette`, checked in `tests/test_music.py`).
   Text is never tinted to match the page.
3. **Type does the work.** Names are set in Inter Black, upper case, as large
   as the space allows (`fit_display`, at most two lines). All other text is
   small and white, at three strengths: `TEXT` (100 %), `TEXT_2` (75 %) and
   `TEXT_3` (53 %). Section labels and overlines are small caps, tracked wide.
4. **Film, not glass.** Grain, dust and light leaks go over the colour. The
   rest of the app uses frosted glass; the Music tab uses print. The one
   exception is the full-screen "now playing" card, which floats over the
   blurred cover.
5. **One white action per page.** The solid white pill (Play, Search) is the
   page's main action. Other actions are icons in thin rings, or thin
   sharp-cornered frames in small caps ("All tracks", "Artist"). Nothing
   competes with the white pill.
6. **Quiet chrome, loud content.** The rail is near-black and neutral, and it
   never takes the page's colour. Only its active marker does (the accent
   bar).

## Colour: reading a picture

`palette_from_image(img)` follows Android's Palette approach:

1. Shrink the picture to 48×48 and quantise it to ten swatches by median
   cut (Pillow's `MEDIANCUT`).
2. Score the swatches against targets (*vibrant*: saturation 1, lightness
   0.5; *dark vibrant*: lightness 0.26; *light vibrant*: lightness 0.74). The
   score weights saturation closeness 0.24, lightness closeness 0.52 and
   population 0.24.
3. The **key** hue is the swatch that covers the most of the picture,
   weighted by how colourful it is. The **second** hue is the most colourful
   swatch at least 25° away from it, and that becomes the glow. A picture
   with no colourful swatch (black-and-white, sepia, grey) gives a neutral
   page in its own slight cast.
4. `make_palette` turns these into five tokens:

| token    | made from                     | used for                                       |
|----------|-------------------------------|------------------------------------------------|
| `base`   | key hue, S 0.30–0.80, L ≤0.38  | the lit side of the page (upper right)         |
| `deep`   | key hue, L 0.105               | the dark side, the lower page, the player bar  |
| `glow`   | second hue, L 0.56             | the radial light behind the picture, the leaks |
| `accent` | vibrant hue, L 0.70            | the rail's marker, the bars of the playing row |
| `ink`    | black or white by contrast     | text on the accent                             |

## Layout

```
┌─ rail 224 ─┬─ stage (painted) ────────────────────────────────────────┐
│ ♪ Music    │  ‹ back                                                   │
│ [search ]  │  OVERLINE · SMALL CAPS                    ┌── picture ──┐ │
│ DISCOVER   │  THE NAME, AS LARGE                       │  melting    │ │
│  Home      │  AS IT FITS                               │  into the   │ │
│  Search    │  line under it                            │  page       │ │
│ YOUR MUSIC │  ( Play ) (⤮) (☆) [ALL TRACKS]            └─────────────┘ │
│  Songs …   │  POPULAR                                                 │
│ MOODS      │  TITLE        TITLE        TITLE                         │
│  Chill …   │  album        album        album                         │
│            │  SONGS ─────────────────────────────── (numbered rows)   │
│            │  ALBUMS ──────── (tiles, as many columns as fit)         │
├────────────┴──────────────────────────────────────────────────────────┤
│ ▔▔▔▔▔ progress hairline                                                │
│ [cover] title / artist ☆     ⤮  ⏮  (▶)  ⏭  ⟳     0:56 / 4:25 ≡ 🎤 ⬇ 🔊── ⤢ │
└───────────────────────────────────────────────────────────────────────┘
```

- Page gutter: 36 px. Header height: 62 % of the visible page, kept between
  360 and 560 px. The rail is 224 px wide and the player is 78 px high.
- Song rows are 50 px. From left to right: number (bars while the song
  plays, ▶ under the cursor), +, a 38 px cover, title over artist, album
  (only from 680 px wide), length, ⬇ and ⋮. A click on the artist or album
  opens that page.
- Tiles are 148–196 px wide, in as many columns as fit, with one or two rows
  until "See all" is clicked. Album covers have 6 px corners; artists are
  circles; moods are coloured cards with their name set in display type.

## Motion

- Page colours cross-fade over about 350 ms (`_Stage`). The header picture
  fades the same way (`Hero.set_art`).
- Tiles lift 3 px and darken slightly under the cursor, and a white play disc
  fades in at their lower right.
- The playing row's three bars bounce at about 14 fps, and only while the
  song plays.
- The full-screen cover drifts and breathes slowly (`_FullView`).

## Rules

- Do not tint text. If a colour fails the white-text contrast check, fix the
  palette, not the text.
- Do not put a second solid white button on a page.
- Do not add icons to the rail.
- Do not frame the header picture. It always melts into the page.
- A page's own picture is always fetched first (`ArtLoader.want(...,
  first=True)`), so it never waits behind a list's thumbnails.
- New pages are `_Page` objects with a `Hero` and sections added through
  `page.add(widget, "Label", "See all")`. Set `page.art_url` (and
  `page.wide_art` for a banner) and call `_paint_page`.
