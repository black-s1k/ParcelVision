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
    """
    Enhance image quality for OCR: denoise, sharpen, boost contrast.
    Returns path to a temp preprocessed file (caller should delete when done).
    """
    img = cv2.imread(image_path)
    if img is None:
        return image_path

    # Upscale small images so text is large enough for OCR
    h, w = img.shape[:2]
    if max(h, w) < 1200:
        scale = 1200 / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)

    # Denoise
    img = cv2.fastNlMeansDenoisingColored(img, None, 10, 10, 7, 21)

    # Convert to LAB and apply CLAHE on L-channel for better contrast
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    img = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)

    # Mild sharpening kernel
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    img = cv2.filter2D(img, -1, kernel)

    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    cv2.imwrite(tmp.name, img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return tmp.name


# ----------------------------------------------------------------------
# --- FALLBACK HELPERS -------------------------------------------------
# ----------------------------------------------------------------------

def guess_parcel_type(image_path: str) -> str:
    """
    Simple color + texture classifier for parcel type.
    """
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
    """
    Try multiple patterns to extract a unit number from OCR text.
    Preserves alphanumeric suffixes (e.g., 204A, 1911B).
    """
    patterns = [
        # Explicit keyword + optional separator + unit (with optional letter suffix)
        r"(?:UNIT|APT|SUITE|APARTMENT|ROOM|RM|#)\s*[:#\-]?\s*(\d{1,5}[A-Z]?)\b",
        # Unit embedded at the start of an address line: "1911B - 123 Main St"
        r"^(\d{2,5}[A-Z]?)\s*[-,]",
        # Unit after a dash in address: "123 Main St - 204A"
        r"-\s*(\d{2,5}[A-Z]?)\s*$",
        # Bare unit on its own line (2-4 digits optionally followed by a letter)
        r"^\s*(\d{2,4}[A-Z]?)\s*$",
        # Fallback: first 2-5 digit sequence (with optional letter) that looks like a unit
        r"\b(\d{2,5}[A-Z]?)\b",
    ]
    for pat in patterns:
        for line in text.splitlines():
            m = re.search(pat, line.strip(), re.IGNORECASE)
            if m:
                return m.group(1).upper()
    return "UNKNOWN"


def _extract_name_from_text(text: str) -> str:
    """
    Extract recipient name from OCR text.
    Looks for 'TO:' blocks first, then falls back to capitalized word pairs.
    """
    # Look for "TO:" label followed by a name on the same or next line
    to_block = re.search(
        r"(?:^|\n)\s*TO\s*:?\s*([A-Z][A-Za-z'\-]{1,}(?:\s+[A-Z][A-Za-z'\-]{1,})+)",
        text, re.MULTILINE
    )
    if to_block:
        return to_block.group(1).strip().title()

    # Capitalized full name (2+ words, allows short names like "Li Wang")
    name_match = re.search(
        r"\b([A-Z][A-Z'\-]{0,}(?:\s+[A-Z][A-Z'\-]{0,})+)\b", text
    )
    if name_match:
        candidate = name_match.group(1).strip()
        # Avoid matching supplier names / common label keywords
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
    """
    Backup OCR extraction using pytesseract + regex if Gemini fails.
    Gracefully handles missing tesseract (returns UNKNOWN for text fields).
    """
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

_GEMINI_PROMPT = """You are reading a shipping/delivery label photo. Extract fields from the RECIPIENT (ship-to) address only — NOT the sender/return/from address.

Return ONLY a JSON object with exactly these four fields:
{
  "unit": "<apartment or suite number>",
  "name": "<recipient full personal name>",
  "supplier": "<courier>",
  "parcel_type": "<color + BOX or PACKAGE>"
}

BUILDING CONTEXT — these labels are for two residential buildings in Toronto:
  • 10 Graphophone Grove  (building number = 10,   apartment units are 3–4 digits, e.g. 1204, 2406)
  • 1285 Dupont St        (building number = 1285,  apartment units are 3–4 digits, e.g. 504, 1106)

ADDRESS FORMAT on these labels often reads:  <UNIT>  <BUILDING#>  <STREET NAME>
  2406 10 GRAPHOPHONE GROVE   →  unit = 2406   (10 is the building, not the unit)
  1507 1285 DUPONT ST         →  unit = 1507   (1285 is the building, not the unit)
  SUITE 804, 10 GRAPHOPHONE   →  unit = 804

UNIT rules:
- Must be 3–4 digits (100–9999), with an optional trailing letter (e.g. 204A).
- The numbers 10 and 1285 are always the building/street number — NEVER the unit.
- Any number under 100 is NOT an apartment unit.
- Canadian postal codes (e.g. M6H 0E5), tracking numbers, and barcodes are NOT units.
- If genuinely absent, return "UNKNOWN".

NAME rules:
- Personal name only (First Last). Never a company, building name, or courier.
- Check the line immediately after "SHIP TO:", "TO:", "ATTN:", or "C/O:".
- If the label has both a company and a person, return the person.
- If only a company name exists, return "UNKNOWN".

SUPPLIER — pick one:
  AMAZON, UPS, FEDEX, DHL, PUROLATOR, INTELCOM, CANPAR, CANADA POST, OTHER

PARCEL TYPE:
- FORM: PACKAGE (soft bag, poly mailer, padded envelope) or BOX (rigid cardboard)
- COLOR: BROWN, WHITE, BLACK, BLUE, PINK, GREY, CLEAR
- Amazon exceptions (only with visible Amazon/Prime branding):
    blue poly mailer  → PRIME BLUE PACKAGE
    orange wrap       → PRIME ORANGE PACKAGE
    brown box         → AMAZON BOX
- Never use "bag", "mailer", or "envelope".

Return ONLY the JSON. No markdown. No explanation."""


def extract_with_gemini(image_path: str) -> Dict:
    """
    Primary extraction via Gemini Vision API with image preprocessing.
    """
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

    # Detect mime type from extension
    ext = os.path.splitext(image_path)[1].lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"

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

    # Try 2.5-flash first (higher quality); fall back to 1.5-flash on quota error
    _MODELS = ["gemini-2.5-flash", "gemini-1.5-flash"]
    response = None
    for model in _MODELS:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )
        print(f"Sending image to Gemini Vision API ({model})...")
        response = requests.post(url, json=payload, timeout=45)
        if response.status_code == 429:
            print(f"[WARN] {model} quota exceeded — trying next model...")
            continue
        break

    if response.status_code != 200:
        raise Exception(f"Gemini API error {response.status_code}: {response.text}")

    result = response.json()
    if not result.get("candidates"):
        raise Exception("No candidates in Gemini response")

    raw = result["candidates"][0]["content"]["parts"][0].get("text", "").strip()

    # Strip markdown code fences if present
    raw = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()

    # Try standard JSON parse first (greedy match to handle multi-line)
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(0))
            return _normalize(data, image_path)
        except json.JSONDecodeError:
            pass

    # Gemini output was truncated — salvage field values with targeted regex
    print(f"[WARN] JSON parse failed — salvaging fields from partial output:\n{raw}")
    data = {}
    for field in ("unit", "name", "supplier", "parcel_type"):
        m = re.search(rf'"{{field}}"\s*:\s*"([^"]*)"', raw)
        if m:
            data[field] = m.group(1)
    if not data:
        raise Exception(f"No valid JSON in Gemini output:\n{raw}")
    return _normalize(data, image_path)


def _normalize(data: Dict, image_path: str) -> Dict:
    """
    Normalize and validate extracted fields.
    Preserves alphanumeric unit suffixes (e.g., 204A).
    """
    # --- Unit ---
    unit_raw = str(data.get("unit", "")).strip().upper()
    unit_match = re.search(r"\b(\d{1,5}[A-Z]?)\b", unit_raw)
    unit_candidate = unit_match.group(1) if unit_match else "UNKNOWN"

    # Reject building/street numbers and numbers too small to be apartment units
    _STREET_NUMBERS = {"10", "1285"}
    if unit_candidate in _STREET_NUMBERS:
        unit_candidate = "UNKNOWN"
    elif unit_candidate.isdigit() and int(unit_candidate) < 100:
        unit_candidate = "UNKNOWN"

    # Reject pure 5-digit numbers (postal codes / zip codes)
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

_FOCUSED_PROMPT = """Look carefully at this shipping label. Focus only on the RECIPIENT (ship-to) block, not the sender/return address.

BUILDING CONTEXT:
  • 10 Graphophone Grove, Toronto  — building number = 10,   apartment units are 3–4 digits
  • 1285 Dupont St, Toronto        — building number = 1285,  apartment units are 3–4 digits

Address lines on these labels often read:  <UNIT> <BUILDING#> <STREET>
  2406 10 GRAPHOPHONE GROVE  →  unit = 2406  (10 is the building, not the unit)
  1507 1285 DUPONT ST        →  unit = 1507  (1285 is the building, not the unit)

Return ONLY:
{"unit": "<3-4 digit apartment number, or UNKNOWN>", "name": "<First Last personal name, or UNKNOWN>"}

Rules:
- Unit must be 100–9999. Never 10, never 1285, never a postal code or tracking number.
- Name must be a personal name. Check after SHIP TO:, ATTN:, C/O:."""


def _retry_focused(image_path: str, current: Dict) -> Dict:
    """
    If unit or name is still UNKNOWN after first pass, do a second focused call.
    """
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
        resp = None
        for model in ("gemini-2.5-flash", "gemini-1.5-flash"):
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={api_key}"
            )
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code == 429:
                print(f"[WARN] Retry: {model} quota exceeded — trying next model...")
                continue
            break
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
    """
    Unified interface: Gemini first → focused retry → fallback OCR.
    """
    print(f"\n{'='*60}")
    print(f"ANALYZING: {os.path.basename(image_path)}")
    print(f"{'='*60}\n")

    try:
        result = extract_with_gemini(image_path)
    except Exception as e:
        print(f"[WARN] Gemini failed: {e}\nUsing fallback OCR...")
        result = fallback_regex_ocr(image_path)

    # Second pass: focused retry for any remaining UNKNOWN fields
    result = _retry_focused(image_path, result)

    # Final fallback fill for any still-missing fields
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
