# Roadmap

Ideas not yet started, roughly in order of value. If the app is going online soon, do
"Public hosting" first.

## Test build with real bricks

Nothing has been built yet, and much rests on assumptions. Order and build a small piece: 4 LEGO
Art panels, pins, the frame, smooth tiles and a raised area. It should answer:

- Do the hidden stacks of a relief hold, and how do they attach across baseplates? Plates stay
  within theirs, so nothing bridges two.
- Does the frame hold, and can it hang? `assembly_parts` matches LEGO's 3 x 3 panel sets; other
  sizes are worked out from them, always with 2 hangers. Above 128 studs, larger than any official
  set found, the guide advises a rigid board.
- How well do smooth tiles stay on, and how hard are they to remove after a mistake?

## Prices from real data

The estimate uses three broad price tiers, giving ranges too wide to plan a purchase
around (e.g. 363–704 €). BrickLink's API could supply real prices per colour, which could
also steer colour choices towards cheaper bricks. On hold: an API key requires a BrickLink
seller account. Prices could instead be updated by hand in `models/pricing.py`, from
BrickLink's public price guide pages. `pricing.PART_PRICE_FACTORS` should come from them too:
it is based on a few listings for black parts only, and is a guess for most of the structure.
The test build's order would give some real prices to compare against.

## Seller stock

A colour in BrickLink's catalog may not be available in the quantity a mosaic needs: up to 1,024
pieces per baseplate. This matters most for gold and silver, and for `Light Pink` → Rose Pink (56)
and other less certain colour ids, to compare by eye. `bricklink.PART_COLORS` also changes over time.

## Build order for large mosaics

Group thousands of pieces by panel and colour in the guide, so that a large build is easier to
sort and to follow.

## Public hosting

- Sessions, saved projects, PDF and depth limits live on one machine's disk and in one process: running
  several Waitress processes or machines needs shared storage and a shared limit.
- Rate-limit each visitor: today one person can still render or upload as often as they like.
- Estimate the running cost of the depth model and of printing PDFs.
- Get legal advice on using the LEGO name, which appears throughout the app and its guides.

## Relief

Side builds for the raised parts, as in LEGO's The Kiss, if the test build shows they're needed.

## Gold and silver accents

- Check by eye where they land: the limits (`ACCENT_SPOT_STUDS`, `ACCENT_MAX_SHARE` and the
  `METAL_*` values in `models/canvas.py`) were only tried on synthetic images and a few photos,
  and may give scattered specks rather than real highlights.
- They only shine in the 3D view: the flat render and the guide show them as plain colours.

## Web app

- Review the interface: step 2 now has too many options and looks complex. Consider simple
  defaults up front, with the rest tucked away under advanced settings.
- Try painting a relief and pinch to zoom on a real touch screen, and the 3D view on weaker
  graphics than an RTX 3060 Ti.
- Offer the frame's structure in another colour than black.

## Documentation

Regenerate the README's images and prices and `docs/example-guide-dog.pdf`, which still show
older looks and prices.
