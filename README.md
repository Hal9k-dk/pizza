# Google Sheets Order Extractor

A simple, lightweight Python script to extract **itemized orders** from a public Google Spreadsheet.

This script intelligently filters the spreadsheet to extract only actual customer orders, skipping:
- Metadata and headers
- Empty order slots
- Payment fees
- Totals and summary rows
- Option lists

## Why this approach?

For **public** Google Sheets, we don't need authentication! This script uses Google's CSV export feature - no API keys, no service accounts, no complexity. Just a spreadsheet URL and `requests`.

## Setup

### 1. Install Dependencies

Using [uv](https://github.com/astral-sh/uv):

```bash
uv sync
```

Or with pip:
```bash
pip install -e .
```

### 2. Configure Your Spreadsheet URL

Add to `.env`:
```
ORDER_SHEET_URL=https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit
```

That's it! No credentials needed.

## Usage

### Print orders to console (readable format)
```bash
extract-orders
```

Shows a nicely formatted summary with:
- Customer name
- Pizza selection
- Modifications/toppings
- Price (numeric and text)
- Payment status (✓/✗)
- Total count and revenue

### Save as JSON
```bash
extract-orders --format json --output orders.json
```

Each order includes:
- `Navn` - Customer name
- `Nr` - Pizza/item number with name
- `Tilbehør` - Modifications/toppings (if any)
- `Pris` - Price as number
- `Pris (tekst)` - Price as text (e.g., "90 kr.")
- `Betalt` - Payment status (true/false)
- Other spreadsheet columns

### Save as CSV
```bash
extract-orders --format csv --output orders.csv
```

### Use custom URL (without .env)
```bash
extract-orders --url https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit
```

## Environment Variables

The script reads `ORDER_SHEET_URL` from `.env`:
```
ORDER_SHEET_URL=https://docs.google.com/spreadsheets/d/...
```

## File Structure

```
pizza/
├── .env                    # Contains ORDER_SHEET_URL
├── pyproject.toml          # Project configuration (PEP 621)
├── extract_orders.py       # Main script
├── uv.lock                 # (Auto-generated) UV lock file
└── README.md              # This file
```

## Development

### Install with dev dependencies

```bash
uv sync --all-extras
```

### Run linting and formatting

```bash
# Format code with black
uv run black extract_orders.py

# Lint with ruff
uv run ruff check extract_orders.py

# Fix issues automatically
uv run ruff check --fix extract_orders.py
```

### Run tests (if added)

```bash
uv run pytest
```

## Why uv?

[uv](https://github.com/astral-sh/uv) is a fast, reliable Python package installer and resolver:
- **10-100x faster** than pip
- **Single tool** for package management, virtual environments, and project configuration
- **PEP 621 compatible** - uses modern `pyproject.toml` instead of multiple config files
- **Lock files** - reproducible dependencies across machines
- **Python version management** - optional built-in Python discovery
