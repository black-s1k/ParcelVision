import gspread
from oauth2client.service_account import ServiceAccountCredentials

def test_google_sheet_connection():
    # Scopes
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]

    # Credentials
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    client = gspread.authorize(creds)

    # Open the sheet by ID
    sheet = client.open_by_key("1kk26zI931UdarkoIgLES08YF4X2w5Y45-43_4aQD3bQ").sheet1

    # Append a test row
    sheet.append_row(["TEST ENTRY", "404", "ANGELA", "AMAZON", "BROWN BOX", "", ""])

    print("Connected and appended a test row.")

if __name__ == "__main__":
    test_google_sheet_connection()