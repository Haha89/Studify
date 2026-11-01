# Roadmap

Ideas not yet started, roughly in order of value. If the app is going online soon, do
"Public hosting" first.

## Prices from real data

The estimate uses three broad price tiers, giving ranges too wide to plan a purchase
around (e.g. 363–704 €). BrickLink's API could supply real prices per colour, which could
also steer colour choices towards cheaper bricks. On hold: an API key requires a BrickLink
seller account. Prices could instead be updated by hand in `models/pricing.py`, from
BrickLink's public price guide pages. `pricing.PART_PRICE_FACTORS` should come from them too:
it is based on a few listings for black parts only.

## Public hosting

- Limit an image's pixel count before decoding: the 15 MB limit is on the file, so a small
  compressed PNG can still decode into a huge image.
- A production server instead of Flask's development server.
- A limit on simultaneous PDF prints, each of which starts a full Chrome.
- DNS rebinding: a host could change its address between the link check and the download.

## Relief

`--relief stepped` hasn't met real bricks. Build a small one to check that the stacks hold,
how they attach across baseplates (plates stay within theirs, so nothing bridges two), and
how it hangs. Then consider letting users correct the detected subject, gold and silver
accents, and side builds for the raised parts.

## Smaller items

- BrickLink accepts our wanted lists, and `bricklink.PART_COLORS` holds its catalog's
  colours for each plate, which change over time. Compare the less certain colour ids by
  eye, notably `Light Pink` → Rose Pink (56). Check whether sellers stock enough of each.
