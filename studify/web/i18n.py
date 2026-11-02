"""The web app in French or English.

Text is written in French in the code, and wrapped in ``_()``: English is looked up
from it here. The language comes from a cookie set by ``/lang/<code>``, then from the
browser's preferences. Outside a request, as in the tests, it is French.
"""

from __future__ import annotations

from flask import has_request_context, request

LANGUAGES = ("fr", "en")
COOKIE = "studify-lang"

ENGLISH = {
    # Choices.
    "Noir": "Black",
    "Blanc": "White",
    "Plat": "Flat",
    "Tuiles rondes": "Round tiles",
    "Relief": "Relief",
    "Profondeur": "Depth",
    "Baseplates": "Baseplates",
    "Panneaux LEGO Art": "LEGO Art panels",
    "Toutes": "All",
    "Officielles LEGO": "Official LEGO",
    "Compatibles": "Compatible",
    "Sans couleurs rares": "No rare colours",
    "Courantes seulement": "Common only",
    "Libre": "Free",
    "Carré": "Square",
    "Naturel": "Natural",
    "Pop art": "Pop art",
    "BD": "Comic",
    # Step 1.
    "Transforme ta photo en mosaïque LEGO, avec son guide de montage baseplate par baseplate.": (
        "Turn your photo into a LEGO mosaic, with its build guide baseplate by baseplate."
    ),
    "Choisis une image": "Choose a picture",
    "Une photo de famille, ton animal, un portrait… PNG, JPEG ou WebP, 15 Mo max.": (
        "A family photo, your pet, a portrait… PNG, JPEG or WebP, 15 MB at most."
    ),
    "Glisse ton image ici": "Drop your picture here",
    "ou clique pour parcourir tes fichiers": "or click to browse your files",
    "Ou colle un lien vers une image": "Or paste a link to a picture",
    "Charger le lien": "Load the link",
    "Rogner l'image (facultatif)": "Crop the picture (optional)",
    "Trace un rectangle sur la photo pour ne garder que cette zone. Tu peux ensuite le déplacer ou l'agrandir.": (
        "Draw a rectangle on the photo to keep only that area. You can then move or resize it."
    ),
    "Réinitialiser": "Reset",
    "Image chargée : {name}": "Picture loaded: {name}",
    "Image entière.": "Whole picture.",
    "Zone gardée : {width:.0f} % de la largeur × {height:.0f} % de la hauteur.": (
        "Area kept: {width:.0f}% of the width × {height:.0f}% of the height."
    ),
    # Step 2.
    "Règle la mosaïque": "Set up the mosaic",
    "La taille fixe le prix : chaque baseplate fait 32 × 32 tenons, soit 25,6 cm de côté.": (
        "Size sets the price: each baseplate is 32 × 32 studs, or 25.6 cm a side."
    ),
    "Taille : {size} baseplate(s) sur le plus grand côté, soit {studs} tenons ≈ {cm:.0f} cm": (
        "Size: {size} baseplate(s) along the longest side, or {studs} studs ≈ {cm:.0f} cm"
    ),
    "Cadre autour de l'image": "Frame around the picture",
    "aucun": "none",
    "{n} tenon(s)": "{n} stud(s)",
    "Nombre de couleurs maximum": "Maximum number of colours",
    "Moins de couleurs : plus simple à commander, et un rendu plus graphique.": (
        "Fewer colours: easier to order, and a more graphic look."
    ),
    "Palette de couleurs": "Colour palette",
    "Les couleurs rares (anciens gris et bruns, pastels…) sont chères et dures à trouver en quantité. "
    "Les écarter change peu le rendu et baisse nettement le prix. Le style Pop art garde sa propre palette.": (
        "Rare colours (old greys and browns, pastels…) are pricey and hard to find in bulk. Leaving them out "
        "barely changes the look and clearly lowers the price. The Pop art style keeps its own palette."
    ),
    "Tramage": "Dithering",
    "Mélange les couleurs pour simuler celles qui manquent. Plus lent à calculer.": (
        "Blends colours to imitate the ones missing. Slower to work out."
    ),
    "Accents or et argent": "Gold and silver accents",
    "En mode Tuiles rondes seulement : quelques touches d'or et d'argent sur les petites zones dorées ou grises. Une grande zone ne change que si c'est très probablement du métal. Sans effet avec une limite de couleurs ou dans les autres modes.": (
        "In Round tiles mode only: a few touches of gold and silver on small golden or grey spots. A large area only changes if it very likely is metal. No effect with a colour limit or in the other modes."
    ),
    "Contours nets": "Sharp edges",
    "Pour les logos et les aplats : chaque tenon prend la couleur qui le couvre le plus, sans teinte "
    "intermédiaire sur les bords. À éviter sur les photos. Sans effet avec le tramage.": (
        "For logos and flat areas: each stud takes the colour covering most of it, with no blend along the edges. "
        "Best avoided on photos. No effect with dithering."
    ),
    "Tailles de tuiles": "Tile sizes",
    "Moins de pièces à poser avec les grandes tuiles, dans le guide et la liste BrickLink, et un prix estimé "
    "plus bas. La 1x1 reste nécessaire pour les tenons isolés.": (
        "Larger tiles mean fewer pieces to place, in the guide and the BrickLink list, and a lower estimated price. "
        "The 1x1 stays, for lone studs."
    ),
    "Mode": "Mode",
    "Plat : uniquement des tuiles lisses carrées et rectangulaires, à même hauteur. Tuiles rondes : une tuile "
    "ronde 1x1 par tenon pour l'image, façon LEGO Art, et le cadre en tuiles lisses 1x4 ; plus de pièces, un prix "
    "estimé plus haut, et la baseplate se voit entre les tuiles, alors choisis-la sombre. Relief : uniquement des "
    "tuiles lisses carrées et rectangulaires, le sujet surélevé sur des plaques noires cachées, son centre plus "
    "haut encore, avec une carte par couche dans le guide. Le sujet est détecté automatiquement et peut être "
    "imprécis ; le relief n'est pas encore testé avec de vraies briques. Profondeur : comme Relief, mais un modèle "
    "estime la distance de chaque zone, jusqu'à 4 plaques cachées ; il se télécharge à la première utilisation "
    "(27 Mo).": (
        "Flat: only smooth square and rectangular tiles, at one height. Round tiles: a round 1x1 tile per stud for "
        "the picture, like LEGO Art, and a frame of smooth 1x4 tiles; more pieces, a higher estimated price, and the "
        "baseplate shows between the tiles, so pick a dark one. Relief: only smooth square and rectangular tiles, "
        "with the subject raised on hidden black plates and its centre higher still, and a map per layer in the "
        "guide. The subject is detected automatically and can be off; relief is untested with real bricks. Depth: "
        "like Relief, but a model guesses how far each area is, up to 4 hidden plates; it downloads on first use "
        "(27 MB)."
    ),
    "Support": "Base",
    "Baseplates : moins cher, mais elles ne se relient pas entre elles, il faut les fixer sur un panneau rigide. "
    "Panneaux LEGO Art : les panneaux 16 x 16 à goujons et le cadre en briques des sets officiels, à accrocher tels "
    "quels jusqu'à environ 1 m ; plus cher, toutes les pièces sont dans la liste. Non testé en vrai.": (
        "Baseplates: cheaper, but they don't connect to each other, so they need fixing to a rigid board. LEGO Art "
        "panels: the pinned 16 x 16 panels and brick frame of the official sets, to hang as they are up to about "
        "1 m; dearer, with every part in the list. Untested for real."
    ),
    "Cartes en couleur": "Maps in colour",
    "Teinte chaque tenon dans le guide. Désactive-le pour économiser l'encre.": (
        "Tints each stud in the guide. Turn it off to save ink."
    ),
    "Officielles LEGO ≈ 9 € pièce, compatibles ≈ 2 €.": "Official LEGO ≈ €9 each, compatible ≈ €2.",
    "Devise": "Currency",
    "Pour l'estimation du prix.": "For the price estimate.",
    "Générer les styles": "Generate the styles",
    # Step 3.
    "Choisis ton style": "Choose your style",
    "Clique sur l'aperçu qui te plaît. Les lignes marquent les baseplates.": (
        "Click the preview you like. The lines mark the baseplates."
    ),
    "Agrandir": "Enlarge",
    "Rendu réaliste": "Realistic view",
    "Vue 3D": "3D view",
    "Modifier le relief": "Edit the relief",
    "{count} couleurs": "{count} colours",
    "{studs} tenons": "{studs} studs",
    "{count} panneaux": "{count} panels",
    "{count} baseplate(s)": "{count} baseplate(s)",
    "{bricks} briques, soit {tiles} tuiles": "{bricks} bricks, or {tiles} tiles",
    "tuiles rondes": "round tiles",
    "tuiles de bordure": "border tiles",
    "tuiles": "tiles",
    "plaques cachées": "hidden plates",
    "pièces d'assemblage": "assembly parts",
    "hors livraison": "excluding shipping",
    "Pièces {price} (+5 % de rab)": "Parts {price} (+5% spares)",
    " · baseplates {price}": " · baseplates {price}",
    "Couleurs rares, chères ou difficiles à trouver : {colors}": "Rare colours, pricey or hard to find: {colors}",
    "Glisse pour tourner, molette ou deux doigts pour zoomer, double-clic pour revenir. "
    "Dos et accroches approximatifs.": (
        "Drag to turn, scroll or use two fingers to zoom, double-click to go back. Back and hangers are approximate."
    ),
    "{style} : vue 3D": "{style}: 3D view",
    "{style} : rendu réaliste": "{style}: realistic view",
    "{style} : modifier le relief": "{style}: edit the relief",
    "Une tuile ronde par tenon, sur une baseplate sombre.": "A round tile per stud, on a dark baseplate.",
    "Tuiles lisses, carrées et rectangulaires, comme dans le guide et la liste BrickLink.": (
        "Smooth square and rectangular tiles, as in the guide and the BrickLink list."
    ),
    " Le sujet est surélevé sur des plaques noires cachées.": " The subject is raised on hidden black plates.",
    " Clique sur l'image pour zoomer.": " Click the picture to zoom.",
    "Le relief est modifié : le prix, la liste et le guide suivent. Clique sur un style pour le choisir.": (
        "The relief has changed, and the price, list and guide follow. Click a style to choose it."
    ),
    # Step 4.
    "Télécharge le guide": "Download the guide",
    "Un PDF A4 : l'aperçu, puis une page par baseplate avec la carte des tenons et la liste des pièces.": (
        "An A4 PDF: the overview, then a page per baseplate with its stud map and list of pieces."
    ),
    "Télécharger le PDF": "Download the PDF",
    "Copier la liste BrickLink": "Copy the BrickLink list",
    "Télécharger la liste": "Download the list",
    "La liste BrickLink contient les pièces choisies et le rab, sans les baseplates. Copie-la, puis colle-la sur la ": (
        "The BrickLink list holds the chosen pieces and spares, without baseplates. Copy it, then paste it on the "
    ),
    "page d'import BrickLink": "BrickLink upload page",
    ", option « Upload BrickLink XML format ».": ', option "Upload BrickLink XML format".',
    "Guide prêt. Le téléchargement ne démarre pas ? ": "Guide ready. Download not starting? ",
    "Clique ici": "Click here",
    # Service messages.
    "Le fichier envoyé est illisible.": "The file sent can't be read.",
    "Image trop lourde (max {size} Mo).": "Picture too large ({size} MB at most).",
    "Le lien doit commencer par http:// ou https://.": "The link must start with http:// or https://.",
    "Ce lien pointe vers une adresse inaccessible.": "This link points to an address that can't be reached.",
    "Impossible de télécharger l'image depuis ce lien.": "Couldn't download the picture from this link.",
    "Ce fichier n'est pas une image reconnue (PNG, JPEG, WebP…).": "This file isn't a known picture (PNG, JPEG, WebP…).",
    "Impossible d'enregistrer cette image.": "Couldn't save this picture.",
    "Session invalide.": "Invalid session.",
    "Image introuvable : elle a peut-être expiré, recharge-la.": "Picture not found: it may have expired, so load it again.",
    "Impossible de rogner cette image.": "Couldn't crop this picture.",
    "Téléchargement du modèle de profondeur, une seule fois : {done:.0f} / {total:.0f} Mo. Les styles suivront.": (
        "Downloading the depth model, just this once: {done:.0f} / {total:.0f} MB. The styles will follow."
    ),
    "Relief par profondeur impossible : {reason}": "Depth relief not possible: {reason}",
    "Style inconnu.": "Unknown style.",
    "Cette mosaïque n'a pas encore été générée.": "This mosaic hasn't been generated yet.",
    "Impossible de dessiner cette mosaïque.": "Couldn't draw this mosaic.",
    "Ce guide n'a pas encore été généré.": "This guide hasn't been generated yet.",
    "Ce relief ne correspond pas à la mosaïque.": "This relief doesn't match the mosaic.",
}


def current_language() -> str:
    """Return the language of this request: its cookie, else its browser's first choice."""
    if not has_request_context():
        return LANGUAGES[0]
    chosen = request.cookies.get(COOKIE)
    if chosen in LANGUAGES:
        return chosen
    accepted = request.accept_languages.best_match(LANGUAGES)
    return accepted or LANGUAGES[0]


def _(text: str) -> str:
    """Return this French text in the language of the current request."""
    return ENGLISH.get(text, text) if current_language() == "en" else text
