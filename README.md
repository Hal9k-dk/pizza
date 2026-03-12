# Pizza Order Automation

Two scripts for automating group pizza orders at Hal9k:

1. **`extract-orders`** — pulls the order list from a public Google Spreadsheet
2. **`place-orders`** — opens a browser and automatically adds every item to the cart on skalborgpizza.dk

## How it works

Orders are collected in a Google Spreadsheet. `extract-orders` downloads it (no API key needed — it uses the public CSV export endpoint) and filters out metadata rows, leaving only the actual orders.

`place-orders` then reads those orders and drives a Playwright browser through the pizza site: scraping the menu, opening each item, selecting the right size/variant, ticking any modification checkboxes, and adding it to the cart. When all items are in, the browser is left open for you to review and submit the order.

## Setup

### 1. Install dependencies

```bash
uv sync
```

Then install the Playwright browser:

```bash
uv run playwright install chromium
```

### 2. Configure `.env`

```
ORDER_SHEET_URL=https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit
PIZZA_PLACE=https://skalborgpizza.dk
```

The spreadsheet must be publicly readable (no credentials are used).

## Usage

### Extract orders

Print a summary to the terminal:

```bash
extract-orders
```

Save as JSON or CSV:

```bash
extract-orders --format json --output orders.json
extract-orders --format csv  --output orders.csv
```

Override the spreadsheet URL without editing `.env`:

```bash
extract-orders --url https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit
```

### Place orders

```bash
place-orders
```

This will:

1. Fetch the current order list from the spreadsheet
2. Open a visible Chromium browser on the pizza site
3. Accept the cookie banner and select pickup (Afhentning)
4. Add every order to the cart, including modifications
5. Leave the browser open — review the cart and submit the order yourself, then press Enter to close the browser

## Spreadsheet format

The script expects a sheet with a header row containing at least these columns:

| Column | Description |
|---|---|
| `Navn` | Customer name |
| `Nr` | Pizza number and name (e.g. `9 - Rose`, `12A - Freyas Pizza`) |
| `Tilbehør` | Comma-separated modifications (e.g. `chili, hvidløg`) |
| `Pris` | Price in kr. |
| `Betalt` | `TRUE`/`FALSE` payment status |

## Project structure

```
pizza/
├── extract_orders.py   # Spreadsheet extraction
├── place_orders.py     # Browser automation
├── pyproject.toml      # Project config and dependencies
├── .env                # ORDER_SHEET_URL and PIZZA_PLACE (not committed)
└── uv.lock             # Locked dependencies
```
