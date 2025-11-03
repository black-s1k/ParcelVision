# 📦 ParcelVision

An AI-powered parcel logging automation tool for concierge desks.  
Captures parcel label via camera → extracts Supplier, Resident Name, Unit, and Parcel Type → appends to Google Sheets automatically.

## 🚀 Setup

1. Create a Google Cloud Project and enable **Google Sheets API**
2. Create a Service Account and download `credentials.json`
3. Share your Google Sheet with the service account email
4. Run:
   ```bash
   cd backend
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   python app.py

Haven't pushed the Javascript script for browser automation due to privacy reasons onsite
   
