# Roadmap

Ideas not yet started, roughly in order of value. If the app is going online soon, do
"Public hosting" first.

## Prices from real data

The estimate uses three broad price tiers, giving ranges too wide to plan a purchase
around (e.g. 363–704 €). BrickLink's API could supply real prices per colour, which could
also steer colour choices towards cheaper bricks. On hold: an API key requires a BrickLink
seller account. Prices could instead be updated by hand in `models/pricing.py`, from
BrickLink's public price guide pages.

## Public hosting

- Limit an image's pixel count before decoding: the 15 MB limit is on the file, so a small
  compressed PNG can still decode into a huge image.
- A production server instead of Flask's development server.
- A limit on simultaneous PDF prints, each of which starts a full Chrome.
- DNS rebinding: a host could change its address between the link check and the download.

## Prices for larger plates

Mosaics use plates up to 1x4 and 2x2 by default, but the estimate still prices 1x1s. Real
prices per size (above) would show whether larger plates save money, and whether the
default should change.

## Smaller items

- BrickLink accepts our wanted lists, and `bricklink.PART_COLORS` holds its catalog's
  colours for each plate, which change over time. Compare the less certain colour ids by
  eye, notably `Light Pink` → Rose Pink (56). Check whether sellers stock enough of each.
