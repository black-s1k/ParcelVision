# ParcelVision

An AI-powered parcel logging tool for concierge desks. Photograph a shipping label — the system extracts the unit number, resident name, courier, and parcel type, logs it to Google Sheets, and queues the unit for automatic entry into 1Valet.

---

## Requirements

- Python 3.10+
- Google Cloud service account with Sheets API enabled
- Gemini API key
- Chrome (for 1Valet automation)
- cloudflared CLI or Tailscale (for remote phone access)

---

## One-time Setup

**1. Clone the repository**

```bash
git clone https://github.com/blacks1k-sc/ParcelVision.git
cd ParcelVision
```

**2. Install Python dependencies**

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**3. Add credentials**

Create `backend/credentials.json` from your Google Cloud service account, then share your Google Sheet with the service account email.

**4. Configure environment variables**

Create `backend/.env`:

```
GEMINI_API_KEY=your_gemini_api_key_here
```

Optionally add a permanent Tailscale URL so the phone can always reach Flask without a tunnel:

```
TAILSCALE_URL=http://your-macbook.tailXXXX.ts.net:5002
```

**5. Enable Chrome remote debugging (once)**

Run Chrome with the debug flag once. After this you can launch Chrome normally — the flag only needs to be set on first launch or via a permanent shortcut.

```bash
# macOS
open -a "Google Chrome" --args --remote-debugging-port=9222

# Linux
google-chrome --remote-debugging-port=9222 &
```

To make this permanent, add `--remote-debugging-port=9222` to your Chrome desktop shortcut.

---

## Starting the Server

From the project root:

```bash
./start.sh
```

This single command:

1. Resolves a public URL for the phone to reach Flask — checks in order: Tailscale (permanent) > cloudflared quick tunnel (URL auto-captured, no editing) > local network IP fallback
2. Writes the resolved URL to `backend/.env` as `SERVER_URL`
3. Starts Flask on port 5002
4. Connects to Chrome via CDP and injects the 1Valet automation script into the 1Valet tab (or tab 3 by index) — no console pasting required

The terminal will print the phone upload URL once everything is ready. Press Ctrl+C to stop all services.

---

## Uploading a Parcel

1. On your phone, open the URL printed by `start.sh`
2. Tap the upload zone to open the camera or pick an image
3. Tap **Analyse and Log Parcel**
4. The result card shows the extracted unit, name, courier, and parcel type
5. The parcel is logged to Google Sheets and queued for 1Valet automatically

---

## 1Valet Automation

The script is injected into Chrome automatically by `start.sh`. It polls Flask every 5 seconds and enters pending unit numbers into the 1Valet "Add Delivery" popup without any manual input.

The 1Valet popup must be open for units to be entered. The script auto-starts 3 seconds after injection.

---

## URL Strategy

| Method | Setup | Permanence |
|---|---|---|
| Tailscale | Install once on Mac + phone + work PC, set `TAILSCALE_URL` in `.env` | Permanent — URL never changes |
| cloudflared | `brew install cloudflare/cloudflare/cloudflared` | URL changes each restart but is auto-captured — no manual editing |
| Local IP | Nothing | Works only if phone and server are on the same Wi-Fi |

---

## Project Structure

```
ParcelVision/
  start.sh                  — one-command startup
  backend/
    app2.py                 — Flask server with /upload and /valet endpoints
    ocr_utils.py            — Gemini Vision extraction + image preprocessing
    vision_utils.py         — thin wrapper around ocr_utils
    sheet_utils.py          — Google Sheets logging
    inject_tab.py           — Chrome CDP injector
    smartlockerscript.js    — 1Valet automation script (SERVER_URL filled at runtime)
    templates/index.html    — admin upload UI
    requirements.txt
    .env                    — API keys and SERVER_URL (not committed)
    credentials.json        — Google service account (not committed)
```
