# ParcelVision — Automated Parcel Intake System

![Python](https://img.shields.io/badge/python-3.11-3776AB?style=flat&logo=python&logoColor=white)
![Status](https://img.shields.io/badge/status-production-brightgreen?style=flat)
![License](https://img.shields.io/badge/license-MIT-blue?style=flat)
![Buildings](https://img.shields.io/badge/buildings-2-orange?style=flat)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Git%20Bash-lightgrey?style=flat)
![Last Commit](https://img.shields.io/github/last-commit/blacks1k-sc/parcelvision?style=flat)

> Built by a CS student working part-time as a concierge. Reverse-engineered a proprietary smart locker system with no public API, no vendor SDK, and no documentation. Approved by the property manager and deployed across two residential buildings.

---

## Table of Contents

- [Live Demo](#live-demo)
- [Overview](#overview)
- [Architecture](#architecture)
- [Upload Pipeline](#upload-pipeline)
- [Multi-Building Routing](#multi-building-routing)
- [OCR Pipeline](#ocr-pipeline)
- [1Valet Integration — No API Required](#1valet-integration--no-api-required)
- [Tech Stack](#tech-stack)
- [Buildings](#buildings)
- [Project Structure](#project-structure)
- [Setup](#setup)

---

## Live Demo

### OCR extraction pipeline — label photo to structured data in under 15 seconds

![ParcelVision OCR Pipeline Demo](./demo.svg)

### Server startup — tunnel + Flask + URL stamp in one command

![ParcelVision Server Startup Demo](./demo_start.svg)

### Full request cycle — photo capture to 1Valet portal entry

![ParcelVision Request Cycle Demo](./demo_cycle.svg)

### 1Valet browser listener — active in G2 tab console

![ParcelVision 1Valet Listener Demo](./demo_valet.svg)

---

## Overview

ParcelVision automates parcel intake at multi-building residential properties. Concierge staff photograph shipping labels from a mobile browser — the system extracts the unit number, recipient name, courier, and parcel type, logs to Google Sheets, and queues the unit for automatic entry into the 1Valet smart locker portal.

**Full cycle: under 15 seconds. Zero manual re-entry.**

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

    P[SSH Tunnel\nserveo.net] -->|reverse proxy :80 -> :5002| B
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
    Note over OCR: Gemini primary -> focused retry -> pytesseract fallback
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
    G -->|Valid JSON| NRM[Normalize + Validate]
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
| Amazon blue poly mailer | `PRIME BLUE PACKAGE` |
| Amazon orange packaging | `PRIME ORANGE PACKAGE` |
| Amazon cardboard box | `AMAZON BOX` |
| Brown cardboard box | `BROWN BOX` |
| White / black / blue / pink / grey soft bag | `<COLOR> PACKAGE` |
| White / black / blue / pink rigid box | `<COLOR> BOX` |
| Transparent poly bag | `CLEAR PACKAGE` |

---

## 1Valet Integration — No API Required

1Valet has no public API. Integration was built by reverse-engineering the browser interface:

1. **DOM inspection** — identified the suite input field by placeholder text, dropdown options by text content and bounding box visibility
2. **Event simulation** — `input`, `change`, and `keydown` events dispatched in sequence to trigger 1Valet's React state updates
3. **Polling queue** — Flask maintains a per-building queue; the injected script polls every 5 seconds, processes one unit at a time, and calls `/valet/complete` to dequeue
4. **Re-focus logic** — after each unit is entered, the input field is re-focused so the popup stays open for the next entry

The script is self-contained — copy it from `smartlockerscript.txt` (G2) or `smartlockerscript_g1.txt` (G1) and paste into the 1Valet browser tab console. `start.sh` stamps the current tunnel URL into both files on every run.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python · Flask · Flask-SocketIO |
| Vision / OCR | Gemini 2.5 Flash · OpenCV · pytesseract |
| Image preprocessing | OpenCV (CLAHE · denoise · sharpen · upscale) |
| Data logging | Google Sheets · gspread · oauth2client |
| Browser automation | Vanilla JS · DOM introspection · event simulation |
| Tunnel / ingress | serveo.net SSH reverse proxy (zero config, zero cost) |
| Async jobs | Threading · in-memory dict queue · HTTP polling |
| Frontend | HTML · CSS · Vanilla JS (no framework) |
| Environment | Windows · Git Bash · Python venv |

---

## Buildings

| ID | Building | Credentials | Script |
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
    ocr_utils.py                Vision pipeline: Gemini -> retry -> pytesseract
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

Creates an SSH tunnel via serveo.net, stamps the public URL into both SmartLocker scripts, and starts Flask on port 5002. The terminal prints the public URL for the phone.

**5. 1Valet automation**

Open the 1Valet "Add Delivery" popup in the browser, then paste the contents of `smartlockerscript.txt` (G2) or `smartlockerscript_g1.txt` (G1) into the browser console. Listener auto-starts in 3 seconds.

---

## Deployment Note

Runs on a Windows concierge desk PC via Git Bash. Flask is exposed publicly via a free serveo.net SSH tunnel — no cloud infrastructure, no DNS config, no cost. Running in production across two residential buildings, approved and adopted as the standard workflow by the property manager.
