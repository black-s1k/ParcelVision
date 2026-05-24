# ParcelVision — Automated Parcel Intake System

> Built by a CS student working part-time as a concierge. Reverse-engineered a proprietary smart locker system with no public API, no vendor SDK, and no documentation. Approved by the property manager and deployed across two residential buildings.

---

## Overview

ParcelVision automates parcel intake at multi-building residential properties. Concierge staff photograph shipping labels from a mobile browser — the system extracts the unit number, recipient name, courier, and parcel type, logs to Google Sheets, and queues the unit for automatic entry into the 1Valet smart locker portal.

**Full cycle: under 15 seconds. Zero manual re-entry.**

---

## Live UI

<div align="center">

| Upload Screen | Extraction Result |
|:---:|:---:|
| Mobile-first dark UI | Real-time result card |
| Camera capture + drag-and-drop | Unit · Name · Courier · Parcel Type |
| Building toggle (G1 / G2) | Auto-logged to Google Sheets |

</div>

The interface is served by Flask and accessible from any phone on the network or via public SSH tunnel — no app install required.

---

## Architecture

```mermaid
graph TD
    A[Phone Browser] -->|POST /upload multipart| B[Flask Server\nport 5002]
    B -->|202 + job_id| A
    A -->|GET /result/job_id every 2s| B

    B --> C[OCR Pipeline\nocr_utils.py]
    C -->|Primary| D[Gemini 2.5 Flash\nVision API]
    C -->|Retry| E[Focused Prompt\nRetry Call]
    C -->|Fallback| F[pytesseract\n+ OpenCV]

    B --> G{Building Router}
    G -->|building=g2| H[G2 Queue\n10 Graphophone Grove]
    G -->|building=g1| I[G1 Queue\n1285 Dupont St]

    H --> J[Google Sheets G2\nvia gspread]
    I --> K[Google Sheets G1\nvia gspread]

    H -->|pending units| L[SmartLocker Script G2\n1Valet Browser Tab]
    I -->|pending units| M[SmartLocker Script G1\n1Valet Browser Tab]

    L -->|DOM automation| N[1Valet Portal G2]
    M -->|DOM automation| O[1Valet Portal G1]

    P[SSH Tunnel\nserveo.net] -->|reverse proxy :80→:5002| B
```

---

## Upload Pipeline

```mermaid
sequenceDiagram
    participant Phone
    participant Flask
    participant OCR
    participant Sheets
    participant Queue

    Phone->>Flask: POST /upload (image + building)
    Flask-->>Phone: 202 { job_id }
    Flask->>OCR: analyze_parcel(image)
    Note over OCR: Gemini primary → focused retry → pytesseract fallback
    OCR-->>Flask: { unit, name, supplier, parcel_type }
    Flask->>Sheets: append_row(building=g1|g2)
    Flask->>Queue: pending_units_queue[building].append(unit)
    Flask-->>Phone: job result via /result/job_id
    loop Every 5s
        Note over Queue: SmartLocker script polls /valet/pending
        Queue-->>Phone: unit data
        Note over Phone: DOM automation types unit into 1Valet popup
    end
```

---

## Multi-Building Routing

```mermaid
graph LR
    UI[Phone UI\nBuilding Toggle]
    UI -->|building=g2| S[Flask Server]
    UI -->|building=g1| S

    S --> QG2[Queue: g2]
    S --> QG1[Queue: g1]

    QG2 --> SHG2[Google Sheets\nGalleria 2]
    QG1 --> SHG1[Google Sheets\nGalleria 1]

    QG2 -->|/valet/pending?building=g2| JSG2[SmartLocker Script G2]
    QG1 -->|/valet/pending?building=g1| JSG1[SmartLocker Script G1]

    JSG2 --> V1G2[1Valet Portal G2]
    JSG1 --> V1G1[1Valet Portal G1]
```

Each building has its own Google Sheet, service account credentials, and 1Valet session. They share one Flask server on one machine — fully isolated at the queue and data layer.

---

## OCR Pipeline

```mermaid
flowchart TD
    IMG[Label Image] --> PRE[Preprocessing\nOpenCV: denoise · CLAHE · sharpen]
    PRE --> G[Gemini 2.5 Flash\nVision API]
    G -->|Valid JSON| NRM[Normalize + Validate\n_normalize]
    G -->|Partial / truncated| SAL[Salvage via regex]
    SAL --> NRM
    NRM -->|unit or name = UNKNOWN| RET[Focused Retry\nSecond Gemini call]
    RET --> NRM2[Normalize]
    NRM2 -->|still UNKNOWN| FB[pytesseract Fallback\n+ color classifier]
    FB --> OUT[Final Result\nunit · name · supplier · parcel_type]
    NRM2 -->|resolved| OUT
```

**Parcel type classification:**

| Type | Detected as |
|---|---|
| Amazon blue poly mailer | PRIME BLUE PACKAGE |
| Amazon orange packaging | PRIME ORANGE PACKAGE |
| Amazon cardboard box | AMAZON BOX |
| Brown cardboard box | BROWN BOX |
| White / black / blue / pink / grey soft bag | `<COLOR> PACKAGE` |
| White / black / blue / pink box | `<COLOR> BOX` |
| Transparent poly bag | CLEAR PACKAGE |

---

## 1Valet Integration — No API Required

1Valet has no public API. Integration was built by reverse-engineering the browser interface:

1. **DOM inspection** — identified the suite input field by placeholder text, dropdown options by text content and bounding box visibility
2. **Event simulation** — `input`, `change`, and `keydown` events dispatched in sequence to trigger 1Valet's React state updates
3. **Polling queue** — Flask maintains a per-building queue; the injected script polls every 5 seconds, processes one unit at a time, and calls `/valet/complete` to dequeue
4. **Re-focus logic** — after each unit is entered, the input field is re-focused so the popup stays open for the next entry

The script is self-contained — copy it from `smartlockerscript.txt` (G2) or `smartlockerscript_g1.txt` (G1) and paste into the 1Valet browser tab console.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python · Flask · Flask-SocketIO |
| Vision / OCR | Gemini 2.5 Flash · OpenCV · pytesseract |
| Image preprocessing | OpenCV (CLAHE · denoise · sharpen) |
| Data logging | Google Sheets · gspread · oauth2client |
| Browser automation | Vanilla JS · DOM introspection · event simulation |
| Tunnel / ingress | serveo.net SSH reverse proxy (zero config) |
| Async jobs | Threading · in-memory dict queue · HTTP polling |
| Frontend | HTML · CSS · Vanilla JS (no framework) |
| Environment | Windows · Git Bash · Python venv |

---

## Buildings

| ID | Building | Sheet | Script |
|---|---|---|---|
| `g2` | Galleria 2 — 10 Graphophone Grove | `credentials.json` | `smartlockerscript.txt` |
| `g1` | Galleria 1 — 1285 Dupont St | `credentials_g1.json` | `smartlockerscript_g1.txt` |

---

## Project Structure

```
ParcelVision/
  start.sh                      one-command startup: tunnel + Flask + URL stamp
  backend/
    app2.py                     Flask server — /upload, /result, /valet endpoints
    ocr_utils.py                Vision pipeline: Gemini → retry → pytesseract
    vision_utils.py             Thin wrapper around ocr_utils
    sheet_utils.py              Google Sheets writer — dual-building support
    smartlockerscript.txt       G2 browser automation (SERVER_URL stamped at runtime)
    smartlockerscript_g1.txt    G1 browser automation
    templates/index.html        Mobile upload UI — dark, camera-first
    requirements.txt
    .env                        API keys — not committed
    credentials.json            G2 service account — not committed
    credentials_g1.json         G1 service account — not committed
    uploads/                    Saved label images
```

---

## Setup

**1. Clone and install**

```bash
git clone https://github.com/blacks1k-sc/ParcelVision.git
cd ParcelVision/backend
python -m venv venv
source venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
```

**2. Create `backend/.env`**

```env
GEMINI_API_KEY=your_key_here

# Galleria 2
SHEET_ID=your_g2_sheet_id
WORKSHEET_NAME=PACKAGES NEW

# Galleria 1
G1_SHEET_ID=your_g1_sheet_id
G1_WORKSHEET_NAME=PACKAGES NEW
G1_CREDENTIALS_PATH=credentials_g1.json
```

**3. Add service account credentials**

Drop `credentials.json` (G2) and `credentials_g1.json` (G1) into `backend/`. Share each Google Sheet with the corresponding service account email.

**4. Start**

```bash
./start.sh
```

This creates an SSH tunnel via serveo.net, stamps the public URL into both SmartLocker scripts, and starts Flask on port 5002. The terminal prints the public URL for the phone.

**5. 1Valet automation**

Open the 1Valet "Add Delivery" popup in the browser tab, then paste the contents of `smartlockerscript.txt` (G2) or `smartlockerscript_g1.txt` (G1) into the browser console. The listener auto-starts in 3 seconds.

---

## Deployment Note

This runs on a Windows concierge desk PC via Git Bash. Flask is exposed publicly via a free serveo.net SSH tunnel — no cloud infrastructure, no DNS config, no cost. The system has been running in production across two residential buildings since deployment.
