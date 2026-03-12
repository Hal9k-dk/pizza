#!/usr/bin/env python3
"""
Extract itemized order list from Google Spreadsheet
Skips metadata, headers, empty slots, and totals.
"""

import os
import sys
import json
import csv
from io import StringIO
from datetime import datetime
from dotenv import load_dotenv

try:
    import requests
except ImportError:
    print("Error: Required packages not found.")
    print("Install them with: uv sync")
    sys.exit(1)


def get_spreadsheet_url():
    """Load Google Spreadsheet URL from .env file"""
    load_dotenv()
    url = os.getenv('ORDER_SHEET_URL')
    if not url:
        raise ValueError("ORDER_SHEET_URL not found in .env file")
    return url


def extract_sheet_id(url):
    """Extract sheet ID from Google Sheets URL"""
    # URL format: https://docs.google.com/spreadsheets/d/{SHEET_ID}/...
    parts = url.split('/d/')
    if len(parts) < 2:
        raise ValueError("Invalid Google Sheets URL")
    sheet_id = parts[1].split('/')[0]
    return sheet_id


def parse_price(price_str):
    """Extract numeric price from price string like '90 kr.' """
    if not price_str or price_str.strip() in ['', ' kr.']:
        return None
    # Remove " kr." and commas, convert to float
    try:
        price = price_str.replace(' kr.', '').replace(',', '.').strip()
        return float(price) if price else None
    except ValueError:
        return None


def extract_orders(url=None, output_format='print', output_file=None):
    """
    Extract itemized order data from a public Google Spreadsheet
    
    Filters out:
    - Metadata rows
    - Column headers
    - Empty order slots
    - Totals and fees
    
    Args:
        url: Google Sheets URL (if None, loads from .env)
        output_format: 'json', 'csv', or 'print'
        output_file: File path to save output (if None, prints to console)
    
    Returns:
        List of orders (as dicts)
    """
    if not url:
        url = get_spreadsheet_url()
    
    sheet_id = extract_sheet_id(url)
    
    try:
        # Download as CSV from Google Sheets public export
        csv_export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
        response = requests.get(csv_export_url, timeout=10)
        response.raise_for_status()
        
        # Google Sheets returns UTF-8 but doesn't declare it in the Content-Type header,
        # so requests defaults to ISO-8859-1. Decode the raw bytes explicitly.
        csv_text = response.content.decode('utf-8')
        
        # Parse CSV into rows
        csv_file = StringIO(csv_text)
        reader = csv.reader(csv_file)
        rows = list(reader)
        
        # Find the header row (contains "Navn")
        header_row_idx = None
        for idx, row in enumerate(rows):
            if row and row[0] == 'Navn':
                header_row_idx = idx
                break
        
        if header_row_idx is None:
            print("Could not find header row in spreadsheet")
            return []
        
        # Extract headers
        headers = rows[header_row_idx]
        
        # Extract only itemized orders
        # Start from row after headers, stop when we hit empty name cells
        orders = []
        for row_idx in range(header_row_idx + 1, len(rows)):
            row = rows[row_idx]
            
            # Need at least a name and a price to be a valid order
            if not row or len(row) < 4:
                continue
            
            name = row[0].strip() if row else ''
            price_str = row[3].strip() if len(row) > 3 else ''
            
            # Skip if no name
            if not name:
                continue
            
            # Skip special rows (totals, fees, options, etc.)
            if name in ['Antal Bestillinger', 'Betalingsgebyr', 'Tilbehørs muligheder', 'TilbehÃ¸rs muligheder']:
                continue
            
            # Skip rows that are just option lists (very long text without a price)
            if 'Begræ' in name or 'BegrÃ¦' in name:  # "Begrænsninger" options
                continue
            
            # Skip if price is empty or invalid
            if not price_str or price_str == ' kr.':
                continue
            
            # Parse price - if it fails, skip this row
            parsed_price = parse_price(price_str)
            if parsed_price is None and ' kr.' not in price_str:
                # Not a valid order row
                continue
            
            # Create order dict from row
            order = {}
            for idx, header in enumerate(headers):
                if idx < len(row):
                    value = row[idx].strip()
                    
                    # Special handling for specific columns
                    if header == 'Pris':
                        # Parse price as float
                        parsed_price = parse_price(value)
                        order[header] = parsed_price
                        order['Pris (tekst)'] = value  # Also keep original text
                    elif header in ['Betalt', 'Lagt i kurven']:
                        # Convert boolean strings
                        order[header] = value == 'TRUE'
                    else:
                        order[header] = value if value else None
                else:
                    order[header] = None
            
            orders.append(order)
        
        if not orders:
            print("No itemized orders found in the spreadsheet")
            return []
        
        # Format output
        if output_format is None:
            return orders  # silent mode — just return the data
        elif output_format == 'json':
            output_data = json.dumps(orders, indent=2, ensure_ascii=False)
        elif output_format == 'csv':
            output_data = format_as_csv(orders)
        else:  # print
            output_data = format_as_text(orders)
        
        # Save or display
        if output_file:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(output_data)
            print(f"✓ {len(orders)} itemized orders extracted to {output_file}")
        else:
            print(output_data)
        
        return orders
        
    except Exception as e:
        print(f"✗ Error extracting data: {e}")
        print("Make sure the spreadsheet URL is correct and the sheet is publicly accessible.")
        sys.exit(1)


def format_as_csv(orders):
    """Format orders as CSV"""
    if not orders:
        return ""
    
    output = []
    csv_file = StringIO()
    writer = csv.DictWriter(csv_file, fieldnames=orders[0].keys())
    writer.writeheader()
    writer.writerows(orders)
    return csv_file.getvalue()


def format_as_text(orders):
    """Format orders as readable text"""
    if not orders:
        return "No orders found"
    
    output = [f"{'='*80}"]
    output.append(f"ITEMIZED ORDERS - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    output.append(f"Total orders: {len(orders)}")
    
    # Calculate totals
    total_price = sum(o.get('Pris') or 0 for o in orders if o.get('Pris'))
    paid_count = sum(1 for o in orders if o.get('Betalt'))
    output.append(f"Total price: {total_price:.2f} kr. | Paid: {paid_count}/{len(orders)}")
    output.append(f"{'='*80}\n")
    
    for idx, order in enumerate(orders, 1):
        output.append(f"#{idx}. {order.get('Navn', 'Unknown')}")
        output.append(f"   Pizza: {order.get('Nr', 'N/A')}")
        if order.get('Tilbehør'):
            output.append(f"   Modifications: {order['Tilbehør']}")
        output.append(f"   Price: {order.get('Pris (tekst)', 'N/A')}")
        output.append(f"   Paid: {'✓' if order.get('Betalt') else '✗'}")
        output.append("")
    
    return "\n".join(output)


def main():
    """Main entry point for the script"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Extract itemized orders from a public Google Spreadsheet"
    )
    parser.add_argument(
        '--format', 
        choices=['json', 'csv', 'print'], 
        default='print',
        help="Output format (default: print)"
    )
    parser.add_argument(
        '--output', '-o',
        help="Output file path"
    )
    parser.add_argument(
        '--url',
        help="Google Sheets URL (overrides .env)"
    )
    
    args = parser.parse_args()
    
    extract_orders(
        url=args.url,
        output_format=args.format,
        output_file=args.output
    )


if __name__ == "__main__":
    main()
