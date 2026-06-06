import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# Galleria 2 (default)
CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials.json")
SHEET_ID         = os.getenv("SHEET_ID",        "1kk26zI931UdarkoIgLES08YF4X2w5Y45-43_4aQD3bQ")
WORKSHEET_NAME   = os.getenv("WORKSHEET_NAME",  "PACKAGES NEW")

# Galleria 1
G1_CREDENTIALS_PATH = os.path.join(BASE_DIR, os.getenv("G1_CREDENTIALS_PATH", "credentials_g1.json"))
G1_SHEET_ID         = os.getenv("G1_SHEET_ID",       "")
G1_WORKSHEET_NAME   = os.getenv("G1_WORKSHEET_NAME", "PACKAGES NEW")


def connect_to_sheet(building: str = "g2"):
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    if building == "g1":
        creds = ServiceAccountCredentials.from_json_keyfile_name(G1_CREDENTIALS_PATH, scope)
        client = gspread.authorize(creds)
        return client.open_by_key(G1_SHEET_ID).worksheet(G1_WORKSHEET_NAME)
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_PATH, scope)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID).worksheet(WORKSHEET_NAME)


def append_row(row_data, building: str = "g2"):
    """
    Append a parcel entry to the Google Sheet.

    Args:
        row_data (list): [timestamp, unit, name, supplier, parcel_type, released?, released_time]
        building (str): 'g1' or 'g2'
    """
    sheet = connect_to_sheet(building)

    if not isinstance(row_data, list):
        raise ValueError(f"Expected list, got {type(row_data)}")

    if len(row_data) != 5:
        raise ValueError(
            f"Expected 5 elements in row_data, got {len(row_data)}. "
            f"Expected: [timestamp, unit, name, supplier, parcel_type]"
        )

    # col_values(1) returns only cells with actual values — ignores pre-formatted
    # checkbox rows that values.append would otherwise treat as "data"
    col_a = sheet.col_values(1)
    next_row = max(len(col_a) + 1, 2)  # at least row 2 (after header)
    # Write A-E (data) + F (boolean False = unchecked checkbox).
    # Empty cells don't match a FALSE filter; boolean false does while still looking
    # like a normal unchecked checkbox when the column has checkbox data-validation.
    sheet.update(f"A{next_row}:F{next_row}", [row_data + [False]], value_input_option="RAW")
    print(f"[{building.upper()}] Added new parcel entry at row {next_row}")
    return row_data


def get_last_entry(building: str = "g2"):
    sheet = connect_to_sheet(building)
    values = sheet.get_all_values()
    if not values or len(values) <= 1:
        return None
    headers = values[0]
    last_row = values[-1]
    return dict(zip(headers, last_row))
