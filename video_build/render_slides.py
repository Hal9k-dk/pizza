from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import textwrap

W, H = 1920, 1080
BG = "#0b1020"
BG2 = "#121a33"
ACCENT = "#4cc9f0"
ACCENT2 = "#7b2cbf"
TEXT = "#f5f7fb"
SUBTLE = "#b8c1d9"
MUTED = "#7f8aac"
CARD = "#16213e"
CARD2 = "#1d2b53"
GOOD = "#2ec4b6"
WARN = "#ffd166"

FONT_REG = "/usr/share/fonts/noto/NotoSans-Regular.ttf"
FONT_BOLD = "/usr/share/fonts/noto/NotoSans-Bold.ttf"

OUT = Path("video_build/slides")
OUT.mkdir(parents=True, exist_ok=True)

slides = [
    {
        "num": "01",
        "eyebrow": "8D-RAPPORT",
        "title": "Hændelse i pizza-bestillingsautomatisering",
        "subtitle": "Fejl udløst efter ændring af URL-konfiguration",
        "bullets": [
            "System: place-orders Playwright-automatisering",
            "Dato: 2026-04-02",
            "Formel gennemgang med root cause, korrigerende handlinger og forebyggelse",
        ],
        "footer": "Intro",
    },
    {
        "num": "02",
        "eyebrow": "EXECUTIVE SUMMARY",
        "title": "Resumé",
        "bullets": [
            "Google Sheet-opdateringen var ikke den direkte fejlårsag",
            "Fejlen lå i navigation på pizzasitet og valg af ordretype",
            "URL-konfigurationen blotlagde skrøbelig URL-håndtering",
            "Popup-flowet for Udbringning eller Afhentning var implementeret for skrøbeligt",
            "Automatiseringen er nu rettet og gjort mere robust",
        ],
        "footer": "D2 overblik",
    },
    {
        "num": "03",
        "eyebrow": "D1",
        "title": "Etabler teamet",
        "bullets": [
            "Undersøgelsesansvarlig: debugging og kodeanalyse",
            "Indberetter: bruger med crash.txt",
            "Reproduktion: kørsel med opdateret konfiguration",
            "Komponenter: extract_orders.py, place_orders.py, .env, Playwright og skalborgpizza.dk",
        ],
        "footer": "Team og scope",
    },
    {
        "num": "04",
        "eyebrow": "D2",
        "title": "Problembeskrivelse",
        "bullets": [
            "Ordrer blev hentet og vist korrekt fra Google Sheets",
            "Fejlen opstod først ved browser-automatiseringen",
            "Crash ved kald til changemenucard(2)",
            "Brugersymptom: scriptet hang ved Udbringning eller Afhentning",
        ],
        "footer": "Symptomer og evidens",
    },
    {
        "num": "05",
        "eyebrow": "D2",
        "title": "Omfang og afgrænsning",
        "bullets": [
            "Påvirket: automatiseret placeringsflow i place_orders.py",
            "Ikke påvirket: udtræk fra Google Sheet og terminalopsummering",
            "Fejlen krævede kombinationen af URL-afvigelse, popup-flow og timing-følsom DOM-adfærd",
        ],
        "footer": "Scope",
    },
    {
        "num": "06",
        "eyebrow": "D3",
        "title": "Midlertidige afhjælpende handlinger",
        "bullets": [
            "Crash-log blev gennemgået for første reelle fejlpunkt",
            "Live-site blev verificeret med Playwright",
            "Der blev tilføjet URL-normalisering, popup-click og bedre readiness-waiting",
            "README og opstartsvalidering blev forbedret",
        ],
        "footer": "Containment",
    },
    {
        "num": "07",
        "eyebrow": "D4A",
        "title": "Direkte årsag",
        "bullets": [
            "Scriptet antog, at PIZZA_PLACE altid sikkert kunne omskrives til /menukort",
            "Scriptet antog, at changemenucard(2) altid kunne bruges direkte",
            "Disse antagelser holdt ikke på tværs af URL-varianter og popup-tilstande",
        ],
        "footer": "Direkte teknisk årsag",
    },
    {
        "num": "08",
        "eyebrow": "D4B",
        "title": "Root cause og escape point",
        "bullets": [
            "Automatiseringen var for afhængig af implicitte antagelser om struktur og timing",
            "Popup-knappen blev ikke prioriteret over det dækkede side-link",
            "Overlay skjult blev fejlagtigt tolket som menu klar",
            "Der manglede tests for popup-flow og ikke-root URL-konfigurationer",
        ],
        "footer": "Root cause",
    },
    {
        "num": "09",
        "eyebrow": "D5",
        "title": "Valgte permanente korrigerende handlinger",
        "bullets": [
            "Normalisering til den kanoniske /menukort-URL",
            "Valg af Afhentning via aktiv popup-kontrol",
            "Eksplicit wait for menu readiness",
            "Forbedret sidevalidering og klarere fejlmeldinger",
            "Oprydning i Ruff-fejl og vedligeholdbarhed",
        ],
        "footer": "Korrigerende handlinger",
    },
    {
        "num": "10",
        "eyebrow": "D6",
        "title": "Implementering og validering",
        "bullets": [
            "place_orders.py fik nye hjælpefunktioner til URL-normalisering og pickup-valg",
            "README blev opdateret med tydeligere konfigurationsforventninger",
            "Kodekvalitetsproblemer blev fjernet i extract_orders.py og pyproject.toml",
            "Løsningen blev valideret mod live-adfærden på websitet",
        ],
        "footer": "Implementeret løsning",
    },
    {
        "num": "11",
        "eyebrow": "D6",
        "title": "Valideringsresultater",
        "bullets": [
            "Popup-knappen udløser de nødvendige backend-kald",
            "Sitet går kortvarigt i en mellemtilstand efter klik",
            "Efter readiness-wait vender menu-items tilbage fuldt",
            "Popup og overlay er væk, når flowet fortsætter",
        ],
        "footer": "Resultater",
    },
    {
        "num": "12",
        "eyebrow": "D7",
        "title": "Forebyg gentagelse",
        "bullets": [
            "Behandl PIZZA_PLACE som basis-host og ikke som vilkårligt deep link",
            "Brug tilstandsbaseret automatisering frem for antagelsesbaseret automatisering",
            "Vent på funktionel sidetilstand, ikke kun visuelle overgange",
            "Tilføj smoke checks for popup-flow, root-URL og readiness efter valg",
        ],
        "footer": "Forebyggelse",
    },
    {
        "num": "13",
        "eyebrow": "D8",
        "title": "Lukning og anerkendelse",
        "bullets": [
            "Sagen er undersøgt, reproduceret, rettet, valideret og dokumenteret",
            "Tak for crash.txt og det præcise hint om hvor scriptet sad fast",
            "Den hurtige opfølgning var afgørende for den endelige diagnose",
        ],
        "footer": "Lukning",
    },
    {
        "num": "14",
        "eyebrow": "KONKLUSION",
        "title": "Afslutning",
        "bullets": [
            "Fejlen udsprang af skrøbelige antagelser om URL, popup-interaktion og readiness",
            "Regnearksopdateringen var samtidig, men ikke den reelle årsag",
            "Rettelser er implementeret, valideret og pushed",
            "Vi beklager forstyrrelsen og har styrket løsningen væsentligt",
        ],
        "footer": "Konklusion",
    },
]


def font(size, bold=False):
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size=size)


def draw_gradient(img):
    px = img.load()
    for y in range(H):
        t = y / (H - 1)
        r1, g1, b1 = (11, 16, 32)
        r2, g2, b2 = (18, 26, 51)
        r = int(r1 * (1 - t) + r2 * t)
        g = int(g1 * (1 - t) + g2 * t)
        b = int(b1 * (1 - t) + b2 * t)
        for x in range(W):
            px[x, y] = (r, g, b)


def add_decor(draw):
    draw.rounded_rectangle((90, 90, W - 90, H - 90), radius=36, outline="#26345e", width=3)
    draw.rounded_rectangle((100, 100, W - 100, 180), radius=26, fill=CARD2)
    draw.rounded_rectangle((120, H - 150, W - 120, H - 110), radius=18, fill="#11192f")
    draw.ellipse((W - 390, -110, W + 80, 360), fill="#1f2f5d")
    draw.ellipse((-180, H - 360, 240, H + 40), fill="#14274e")
    draw.rounded_rectangle((1400, 130, 1750, 145), radius=8, fill=ACCENT)
    draw.rounded_rectangle((1400, 158, 1660, 170), radius=6, fill=ACCENT2)


def wrap(draw, text, fnt, max_width):
    words = text.split()
    lines = []
    cur = ""
    for word in words:
        test = word if not cur else cur + " " + word
        if draw.textlength(test, font=fnt) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def draw_bullets(draw, bullets, x, y, w):
    bullet_font = font(36, bold=False)
    bullet_gap = 18
    line_gap = 10
    current_y = y
    for bullet in bullets:
        lines = wrap(draw, bullet, bullet_font, w - 50)
        draw.rounded_rectangle((x, current_y + 12, x + 16, current_y + 28), radius=6, fill=ACCENT)
        text_y = current_y
        for line in lines:
            draw.text((x + 34, text_y), line, font=bullet_font, fill=TEXT)
            text_y += 46
        current_y = text_y + bullet_gap
    return current_y


def make_slide(spec):
    img = Image.new("RGB", (W, H), BG)
    draw_gradient(img)
    draw = ImageDraw.Draw(img)
    add_decor(draw)

    draw.text((150, 125), spec["eyebrow"], font=font(28, bold=True), fill=ACCENT)
    draw.text((150, 230), spec["title"], font=font(60, bold=True), fill=TEXT)

    if spec.get("subtitle"):
        subtitle_lines = wrap(draw, spec["subtitle"], font(32), 1100)
        sy = 315
        for line in subtitle_lines:
            draw.text((150, sy), line, font=font(32), fill=SUBTLE)
            sy += 44
        start_y = sy + 20
    else:
        start_y = 345

    # content card
    draw.rounded_rectangle((130, 380, W - 130, H - 190), radius=28, fill=CARD, outline="#24335f", width=2)
    draw.text((170, 415), f"Slide {spec['num']}", font=font(24, bold=True), fill=MUTED)
    draw_bullets(draw, spec["bullets"], 175, 470, W - 360)

    # footer
    draw.text((150, H - 143), "8D root cause analyse og korrigerende handlinger", font=font(24), fill=MUTED)
    draw.text((W - 280, H - 143), spec["footer"], font=font(24, bold=True), fill=GOOD)
    draw.text((W - 220, 125), spec["num"], font=font(34, bold=True), fill=TEXT)

    img.save(OUT / f"slide_{spec['num']}.png", quality=95)


for slide in slides:
    make_slide(slide)

print(f"Generated {len(slides)} slides in {OUT}")
