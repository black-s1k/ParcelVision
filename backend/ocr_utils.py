"""
Parcel label extraction using Google Gemini Vision API + local OCR/color fallback.
Prioritizes local suppliers: Amazon, UPS, FedEx, UNI, Dragonfly, Emile, FleetOptics.
"""

import os
import base64
import requests
import json
import re
import tempfile
from typing import Dict
import cv2
import numpy as np
import pytesseract


# ----------------------------------------------------------------------
# --- IMAGE PREPROCESSING ----------------------------------------------
# ----------------------------------------------------------------------

def preprocess_image(image_path: str) -> str:
    img = cv2.imread(image_path)
    if img is None:
        return image_path

    h, w = img.shape[:2]
    if max(h, w) < 1200:
        scale = 1200 / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)

    img = cv2.fastNlMeansDenoisingColored(img, None, 10, 10, 7, 21)

    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    img = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)

    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    img = cv2.filter2D(img, -1, kernel)

    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    cv2.imwrite(tmp.name, img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return tmp.name


# ----------------------------------------------------------------------
# --- FALLBACK HELPERS -------------------------------------------------
# ----------------------------------------------------------------------

def guess_parcel_type(image_path: str) -> str:
    img = cv2.imread(image_path)
    if img is None:
        return "BROWN BOX"

    avg_color = cv2.mean(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))[:3]
    r, g, b = avg_color

    if max(r, g, b) < 60:
        color = "BLACK"
    elif r > 200 and g > 200 and b > 200:
        color = "WHITE"
    elif b > r + 30 and b > g + 20 and b > 100:
        color = "BLUE"
    elif r > 180 and b > 140 and g < r - 40:
        color = "PINK"
    elif abs(r - g) < 20 and abs(g - b) < 20 and r > 100:
        color = "GREY"
    else:
        color = "BROWN"

    edges = cv2.Canny(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 100, 200)
    edge_density = np.sum(edges > 0) / edges.size
    pkg_type = "BOX" if edge_density > 0.08 else "PACKAGE"

    return f"{color} {pkg_type}".upper()


def _extract_unit_from_text(text: str) -> str:
    patterns = [
        r"(?:UNIT|APT|SUITE|APARTMENT|ROOM|RM|#)\s*[:#\-]?\s*(\d{1,5}[A-Z]?)\b",
        r"^(\d{2,5}[A-Z]?)\s*[-,]",
        r"-\s*(\d{2,5}[A-Z]?)\s*$",
        r"^\s*(\d{2,4}[A-Z]?)\s*$",
        r"\b(\d{2,5}[A-Z]?)\b",
    ]
    for pat in patterns:
        for line in text.splitlines():
            m = re.search(pat, line.strip(), re.IGNORECASE)
            if m:
                return m.group(1).upper()
    return "UNKNOWN"


def _extract_name_from_text(text: str) -> str:
    to_block = re.search(
        r"(?:^|\n)\s*TO\s*:?\s*([A-Z][A-Za-z'\-]{1,}(?:\s+[A-Z][A-Za-z'\-]{1,})+)",
        text, re.MULTILINE
    )
    if to_block:
        return to_block.group(1).strip().title()

    name_match = re.search(
        r"\b([A-Z][A-Z'\-]{0,}(?:\s+[A-Z][A-Z'\-]{0,})+)\b", text
    )
    if name_match:
        candidate = name_match.group(1).strip()
        skip_words = {
            "AMAZON", "FEDEX", "UPS", "DHL", "PUROLATOR", "CANADA POST",
            "CANPAR", "INTELCOM", "UNIT", "SUITE", "APT", "STREET", "AVENUE",
            "ROAD", "DRIVE", "BLVD", "RETURN", "SENDER", "RECIPIENT",
        }
        words = candidate.split()
        if not any(w in skip_words for w in words):
            return candidate.title()

    return "UNKNOWN"


def fallback_regex_ocr(image_path: str) -> Dict:
    preprocessed = preprocess_image(image_path)
    text = ""
    try:
        config = r"--oem 3 --psm 6"
        text = pytesseract.image_to_string(preprocessed, config=config).upper()
    except (FileNotFoundError, Exception) as e:
        print(f"[WARN] pytesseract unavailable: {e}")
    finally:
        if preprocessed != image_path:
            try:
                os.unlink(preprocessed)
            except OSError:
                pass

    if not text:
        return {
            "unit": "UNKNOWN",
            "name": "UNKNOWN",
            "supplier": "OTHER",
            "parcel_type": guess_parcel_type(image_path),
        }

    suppliers_priority = [
        "AMAZON", "UPS", "FEDEX", "UNI", "DRAGONFLY", "EMILE", "FLEETOPTICS",
        "DHL", "PUROLATOR", "INTELCOM", "CANPAR", "CANADA POST"
    ]
    supplier = next((s for s in suppliers_priority if s in text), "OTHER")

    return {
        "unit": _extract_unit_from_text(text),
        "name": _extract_name_from_text(text),
        "supplier": supplier,
        "parcel_type": guess_parcel_type(image_path),
    }


# ----------------------------------------------------------------------
# --- GEMINI EXTRACTION ------------------------------------------------
# ----------------------------------------------------------------------

_GEMINI_PROMPT = """You are reading a shipping/delivery label photo. Your job is to extract key fields from the RECIPIENT (delivery-to) address — NOT the sender/return address.

Extract and return ONLY a JSON object with exactly these fields:

{
  "unit": "<apartment, suite, or unit number — 2-4 digits with optional letter, e.g. 204, 1911, 204A, 1911B>",
  "name": "<recipient's full personal name, e.g. John Smith — NOT a company name>",
  "supplier": "<courier name>",
  "parcel_type": "<color + type, e.g. BROWN BOX, WHITE PACKAGE, GREY PACKAGE>"
}

Rules for "unit":
- The unit/suite number is a SHORT number (typically 2-4 digits, e.g. 204, 1011, 1911).
- The civic/street number (e.g. the "10" in "10 Graphophone Grove" or "1285" in "1285 Dupont St") is NOT the unit. Do NOT return the street number as the unit.
- Canadian condo addresses often use the format "UNIT# - STREET# Street Name". Example: "2401 - 10 Graphophone Grove" means unit=2401, street=10. The unit is the LARGER number BEFORE the dash.
- If the address line contains both a street number and a unit (e.g. "1285 Dupont St, Suite 204"), return only the suite/unit portion (204).
- Look for keywords: APT, UNIT, SUITE, # — or a number appearing AFTER the street name.
- Canadian postal codes (e.g. M5V 3A8, M6H 0E5) are NOT unit numbers.
- Include any trailing letter suffix (204A stays 204A).
- If no unit found, use "UNKNOWN".

Rules for "name":
- Must be a personal name (First Last). NOT a company, building, or courier name.
- Look for prefixes like "ATTN:", "C/O:", "Attention:", or "Care of:" — the name immediately follows.
- If the label shows both a company and a person's name, return the person's name.
- If only a company name is present (no individual), use "UNKNOWN".

Rules for "supplier":
- Match courier branding, logo, or label text.
- Known couriers: AMAZON, UPS, FEDEX, UNI, DRAGONFLY, EMILE, FLEETOPTICS, DHL, PUROLATOR, INTELCOM, CANPAR, CANADA POST.
- If the courier is clearly readable but not in the list above, return it exactly as it appears (e.g. UNIQLO, ECOMLOGISTICS, FOXNDGROUND).
- If unidentifiable, use "OTHER".

Rules for "parcel_type":
- Identify the COLOR and FORM of the physical packaging.
- FORM: Use "PACKAGE" for soft bags, poly mailers, padded envelopes, or plastic pouches. Use "BOX" for rigid cardboard boxes.
- COLOR options: BROWN, WHITE, BLACK, BLUE, PINK, GREY, CLEAR
- Use "CLEAR PACKAGE" for transparent or see-through plastic poly bags.
- Amazon-specific exceptions (only when Amazon/Prime branding is visible):
  - Amazon blue poly mailer or bag -> "PRIME BLUE PACKAGE"
  - Amazon orange packaging -> "PRIME ORANGE PACKAGE"
  - Amazon brown cardboard box with Prime/Amazon logo -> "AMAZON BOX"
- Standard examples: "BROWN BOX", "WHITE PACKAGE", "BLACK PACKAGE", "BLUE BOX", "PINK PACKAGE", "GREY PACKAGE", "BLACK BOX"
- NEVER use words like "bag", "polybag", "mailer", "envelope" — always use PACKAGE or BOX.
- Return ONLY the JSON object. No markdown, no explanation."""


def extract_with_gemini(image_path: str) -> Dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")

    preprocessed = preprocess_image(image_path)
    try:
        with open(preprocessed, "rb") as f:
            image_data = base64.b64encode(f.read()).decode()
    finally:
        if preprocessed != image_path:
            try:
                os.unlink(preprocessed)
            except OSError:
                pass

    ext = os.path.splitext(image_path)[1].lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={api_key}"
    )

    payload = {
        "contents": [{
            "parts": [
                {"text": _GEMINI_PROMPT},
                {"inline_data": {"mime_type": mime, "data": image_data}},
            ]
        }],
        "generationConfig": {
            "temperature": 0,
            "topP": 1,
            "topK": 1,
            "maxOutputTokens": 1024,
        },
    }

    print("Sending image to Gemini Vision API...")
    response = requests.post(url, json=payload, timeout=45)

    if response.status_code != 200:
        raise Exception(f"Gemini API error {response.status_code}: {response.text}")

    result = response.json()
    if not result.get("candidates"):
        raise Exception("No candidates in Gemini response")

    raw = result["candidates"][0]["content"]["parts"][0].get("text", "").strip()
    raw = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()

    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(0))
            return _normalize(data, image_path)
        except json.JSONDecodeError:
            pass

    print(f"[WARN] JSON parse failed — salvaging fields from partial output:\n{raw}")
    data = {}
    for field in ("unit", "name", "supplier", "parcel_type"):
        m = re.search(rf'"{field}"\s*:\s*"([^"]*)"', raw)
        if m:
            data[field] = m.group(1)
    if not data:
        raise Exception(f"No valid JSON in Gemini output:\n{raw}")
    return _normalize(data, image_path)


def _normalize(data: Dict, image_path: str) -> Dict:
    # --- Unit ---
    unit_raw = str(data.get("unit", "")).strip().upper()
    unit_match = re.search(r"\b(\d{1,5}[A-Z]?)\b", unit_raw)
    unit_candidate = unit_match.group(1) if unit_match else "UNKNOWN"

    _STREET_NUMBERS = {"10", "1285"}
    if unit_candidate in _STREET_NUMBERS:
        unit_candidate = "UNKNOWN"

    if re.fullmatch(r"\d{5}", unit_candidate):
        unit_candidate = "UNKNOWN"

    data["unit"] = unit_candidate

    # --- Name ---
    name = str(data.get("name", "")).strip()
    data["name"] = name.title() if name and name.upper() != "UNKNOWN" else "UNKNOWN"

    # --- Supplier ---
    supplier = str(data.get("supplier", "OTHER")).strip().upper()
    data["supplier"] = supplier if supplier else "OTHER"

    # --- Parcel type ---
    data["parcel_type"] = str(data.get("parcel_type", "")).strip().upper() or guess_parcel_type(image_path)

    return data


# ----------------------------------------------------------------------
# --- RETRY WITH FOCUSED PROMPT ----------------------------------------
# ----------------------------------------------------------------------

_FOCUSED_PROMPT = """Look very carefully at this shipping label image.

I need ONLY these two fields from the DELIVERY/RECIPIENT address block (ignore the return/sender address):

1. The apartment/suite/unit number:
   - It is a SHORT number, typically 2-4 digits (e.g. 204, 1011, 1911, 204A).
   - The street/civic number at the START of an address line (e.g. "10" in "10 Graphophone Grove") is NOT the unit.
   - Canadian condo format: "UNIT# - STREET# Street Name" — the unit is the LARGER number BEFORE the dash.
   - Look for it AFTER keywords APT, UNIT, SUITE, # — or as a number appearing after the street name.
   - Canadian postal codes (e.g. M5V 3A8) are NOT unit numbers.
   - If genuinely not found, return "UNKNOWN".

2. The recipient's full personal name (First Last) — NOT a company name.
   - Check for "ATTN:", "C/O:", or "Attention:" prefixes — the name follows immediately.
   - If only a company name exists (no individual), return "UNKNOWN".

Return ONLY JSON:
{"unit": "<unit number or UNKNOWN>", "name": "<full name or UNKNOWN>"}"""


def _retry_focused(image_path: str, current: Dict) -> Dict:
    if current.get("unit") != "UNKNOWN" and current.get("name") != "UNKNOWN":
        return current

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return current

    preprocessed = preprocess_image(image_path)
    try:
        with open(preprocessed, "rb") as f:
            image_data = base64.b64encode(f.read()).decode()
    finally:
        if preprocessed != image_path:
            try:
                os.unlink(preprocessed)
            except OSError:
                pass

    ext = os.path.splitext(image_path)[1].lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={api_key}"
    )

    payload = {
        "contents": [{
            "parts": [
                {"text": _FOCUSED_PROMPT},
                {"inline_data": {"mime_type": mime, "data": image_data}},
            ]
        }],
        "generationConfig": {"temperature": 0, "topP": 1, "topK": 1, "maxOutputTokens": 512},
    }

    try:
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code != 200:
            return current
        result = resp.json()
        if not result.get("candidates"):
            return current
        raw = result["candidates"][0]["content"]["parts"][0].get("text", "").strip()
        raw = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        jm = re.search(r"\{.*?\}", raw, re.DOTALL)
        if not jm:
            return current
        retry_data = json.loads(jm.group(0))

        if current.get("unit") == "UNKNOWN":
            unit_raw = str(retry_data.get("unit", "")).strip().upper()
            m = re.search(r"\b(\d{1,5}[A-Z]?)\b", unit_raw)
            if m:
                current["unit"] = m.group(1)
                print(f"  Retry resolved unit: {current['unit']}")

        if current.get("name") == "UNKNOWN":
            name = str(retry_data.get("name", "")).strip()
            if name and name.upper() not in ("UNKNOWN", ""):
                current["name"] = name.title()
                print(f"  Retry resolved name: {current['name']}")
    except Exception as e:
        print(f"  [WARN] Focused retry failed: {e}")

    return current


# ----------------------------------------------------------------------
# --- MAIN WRAPPER -----------------------------------------------------
# ----------------------------------------------------------------------

def extract_data(image_path: str) -> Dict:
    print(f"\n{'='*60}")
    print(f"ANALYZING: {os.path.basename(image_path)}")
    print(f"{'='*60}\n")

    try:
        result = extract_with_gemini(image_path)
    except Exception as e:
        print(f"[WARN] Gemini failed: {e}\nUsing fallback OCR...")
        result = fallback_regex_ocr(image_path)

    result = _retry_focused(image_path, result)

    for key in ["unit", "name", "supplier", "parcel_type"]:
        if not result.get(key) or result[key] == "UNKNOWN":
            print(f"[WARN] {key} still unknown — filling via OCR fallback...")
            backup = fallback_regex_ocr(image_path)
            if backup.get(key) and backup[key] != "UNKNOWN":
                result[key] = backup[key]

    print(f"\n{'='*60}")
    print("FINAL EXTRACTION RESULT")
    print(f"{'='*60}")
    print(f"  Unit:        {result.get('unit', 'UNKNOWN')}")
    print(f"  Name:        {result.get('name', 'UNKNOWN')}")
    print(f"  Supplier:    {result.get('supplier', 'UNKNOWN')}")
    print(f"  Type:        {result.get('parcel_type', 'UNKNOWN')}")
    print(f"{'='*60}\n")

    return result


# ----------------------------------------------------------------------
# --- CLI ENTRY --------------------------------------------------------
# ----------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python ocr_utils.py <image_path>")
        sys.exit(1)

    path = sys.argv[1]
    if not os.path.exists(path):
        print(f"File not found: {path}")
        sys.exit(1)

    result = extract_data(path)
    print("\nJSON OUTPUT:")
    print(json.dumps(result, indent=2))
