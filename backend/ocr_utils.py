"""Parcel label extraction via Gemini Vision, with a local OCR fallback."""

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


# Civic address per building. Knowing it turns "which of these two numbers is
# the unit?" into a lookup, and lets us reject the civic number if it comes back.
_BUILDING_INFO = {
    "g1": {
        "name": "1285 Dupont St, Toronto",
        "civic": "1285",
        "unit_examples": [
            ("1106-1285 DUPONT ST", "1106"),
            ("1285 DUPONT ST, UNIT 504", "504"),
            ("SUITE 207 - 1285 DUPONT ST", "207"),
        ],
    },
    "g2": {
        "name": "10 Graphophone Grove, Toronto",
        "civic": "10",
        "unit_examples": [
            ("504-10 GRAPHOPHONE GROVE", "504"),
            ("2406 10 GRAPHOPHONE GROVE", "2406"),
            ("SUITE 804, 10 GRAPHOPHONE GROVE", "804"),
        ],
    },
}

# Neither civic number is ever a valid unit. Reject both, since a mis-sorted
# parcel can carry the other building's label.
_CIVIC_NUMBERS = {info["civic"] for info in _BUILDING_INFO.values()}


def _building_info(building: str) -> dict:
    return _BUILDING_INFO.get(building, _BUILDING_INFO["g2"])


def preprocess_image(image_path: str) -> str:
    """Denoise, sharpen and boost contrast. Returns a temp file path."""
    img = cv2.imread(image_path)
    if img is None:
        return image_path

    # Upscale small images so the text is large enough for OCR
    h, w = img.shape[:2]
    if max(h, w) < 1200:
        scale = 1200 / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)

    # Denoise
    img = cv2.fastNlMeansDenoisingColored(img, None, 10, 10, 7, 21)

    # CLAHE on the L channel for contrast
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    img = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)

    # Sharpen
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    img = cv2.filter2D(img, -1, kernel)

    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    cv2.imwrite(tmp.name, img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return tmp.name


def guess_parcel_type(image_path: str) -> str:
    """Classify the parcel by average colour and edge density."""
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


def _is_civic_number(candidate: str) -> bool:
    return candidate in _CIVIC_NUMBERS


def _extract_unit_from_text(text: str, building: str = "g2") -> str:
    """Pull a unit number out of OCR text, keeping any letter suffix."""
    patterns = [
        # UNIT 204A, APT 204, #204
        r"(?:UNIT|APT|SUITE|APARTMENT|ROOM|RM|#)\s*[:#\-]?\s*(\d{1,5}[A-Z]?)\b",
        # 1911B - 123 Main St
        r"^(\d{2,5}[A-Z]?)\s*[-,]",
        # 123 Main St - 204A
        r"-\s*(\d{2,5}[A-Z]?)\s*$",
        # Bare unit on its own line
        r"^\s*(\d{2,4}[A-Z]?)\s*$",
        # Last resort: any 2-5 digit run
        r"\b(\d{2,5}[A-Z]?)\b",
    ]
    for pat in patterns:
        for line in text.splitlines():
            m = re.search(pat, line.strip(), re.IGNORECASE)
            if m:
                candidate = m.group(1).upper()
                if _is_civic_number(candidate):
                    continue
                return candidate
    return "UNKNOWN"


def _extract_name_from_text(text: str) -> str:
    """Pull the recipient name out of OCR text."""
    # TO: followed by a name
    to_block = re.search(
        r"(?:^|\n)\s*TO\s*:?\s*([A-Z][A-Za-z'\-]{1,}(?:\s+[A-Z][A-Za-z'\-]{1,})+)",
        text, re.MULTILINE
    )
    if to_block:
        return to_block.group(1).strip().title()

    # Capitalised full name, two words or more
    name_match = re.search(
        r"\b([A-Z][A-Z'\-]{0,}(?:\s+[A-Z][A-Z'\-]{0,})+)\b", text
    )
    if name_match:
        candidate = name_match.group(1).strip()
        # Skip couriers and address keywords
        skip_words = {
            "AMAZON", "FEDEX", "UPS", "DHL", "PUROLATOR", "CANADA POST",
            "CANPAR", "INTELCOM", "UNIT", "SUITE", "APT", "STREET", "AVENUE",
            "ROAD", "DRIVE", "BLVD", "RETURN", "SENDER", "RECIPIENT",
        }
        words = candidate.split()
        if not any(w in skip_words for w in words):
            return candidate.title()

    return "UNKNOWN"


def fallback_regex_ocr(image_path: str, building: str = "g2") -> Dict:
    """Tesseract + regex extraction, used when Gemini fails."""
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
        "unit": _extract_unit_from_text(text, building),
        "name": _extract_name_from_text(text),
        "supplier": supplier,
        "parcel_type": guess_parcel_type(image_path),
    }


def _build_main_prompt(building: str) -> str:
    info = _building_info(building)
    examples = "\n".join(f"  {label:<32} →  unit = {unit}" for label, unit in info["unit_examples"])
    return f"""You are reading a shipping/delivery label photo. This parcel was scanned at **{info['name']}** — its civic/street number is **{info['civic']}**. Extract fields from the RECIPIENT (ship-to) address only — NOT the sender/return/from address.

Return a JSON object with exactly these four fields: unit, name, supplier, parcel_type.

UNIT — how to find it:
- The address line pairs TWO numbers: the civic number {info['civic']} (the building itself) and the apartment/unit number. They can appear in either order and are often joined by a hyphen:
{examples}
- The unit is always the OTHER number — {info['civic']} itself is NEVER the unit.
- Apartment/unit numbers here are normally 3–4 digits (100–9999), with an optional trailing letter (e.g., 204A).
- Canadian postal codes (e.g., M6H 0E5), tracking numbers, and barcodes are NOT units.
- BE CONSERVATIVE: if you cannot find a clear, confident unit number — it's blurry, only {info['civic']} is visible, or the second number looks like a postal code/tracking number — return "UNKNOWN". A wrong guess sends the parcel to the wrong door, so "UNKNOWN" is always better than an unconfident guess.

NAME rules:
- Personal name only (First Last). Never a company, building name, or courier.
- Check the line immediately after "SHIP TO:", "TO:", "ATTN:", or "C/O:".
- If the label shows both a company and a person, return the person.
- If only a company name exists, return "UNKNOWN".

SUPPLIER — pick exactly one:
  AMAZON, UPS, FEDEX, DHL, PUROLATOR, INTELCOM, CANPAR, CANADA POST, OTHER

PARCEL TYPE — describe the physical parcel shown in the photo (not the label):
- FORM: PACKAGE (soft bag, poly mailer, padded envelope) or BOX (rigid cardboard)
- COLOR: BROWN, WHITE, BLACK, BLUE, PINK, GREY, CLEAR
- Amazon exceptions (only with visible Amazon/Prime branding):
    blue poly mailer  → PRIME BLUE PACKAGE
    orange wrap       → PRIME ORANGE PACKAGE
    brown box         → AMAZON BOX
- Never use "bag", "mailer", or "envelope"."""


def _build_focused_prompt(building: str) -> str:
    info = _building_info(building)
    examples = "\n".join(f"  {label:<32} →  unit = {unit}" for label, unit in info["unit_examples"])
    return f"""Look carefully at this shipping label. This parcel was scanned at {info['name']} (civic number {info['civic']}). Focus only on the RECIPIENT (ship-to) block, not the sender/return address.

The address line pairs the civic number {info['civic']} with the apartment/unit number, in either order, often hyphenated:
{examples}

Return a JSON object with exactly two fields: unit, name.
- unit: the 3-4 digit apartment number (never {info['civic']}, never a postal code/tracking number), or "UNKNOWN" if you're not confident.
- name: the recipient's personal "First Last" name (check after SHIP TO:, ATTN:, C/O:), or "UNKNOWN"."""


_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "unit": {
            "type": "STRING",
            "description": "3-4 digit apartment/unit number (optionally with a trailing letter), or UNKNOWN. Never the building's civic number.",
        },
        "name": {
            "type": "STRING",
            "description": "Recipient personal full name (First Last), or UNKNOWN.",
        },
        "supplier": {
            "type": "STRING",
            "description": "One of AMAZON, UPS, FEDEX, DHL, PUROLATOR, INTELCOM, CANPAR, CANADA POST, OTHER.",
        },
        "parcel_type": {
            "type": "STRING",
            "description": "Color + BOX or PACKAGE, e.g. BROWN BOX, WHITE PACKAGE, PRIME BLUE PACKAGE.",
        },
    },
    "required": ["unit", "name", "supplier", "parcel_type"],
}

_FOCUSED_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "unit": {"type": "STRING"},
        "name": {"type": "STRING"},
    },
    "required": ["unit", "name"],
}

# Better model first, cheaper one on quota errors
_MODELS = ["gemini-2.5-flash", "gemini-1.5-flash"]


def _gemini_keys():
    """GEMINI_API_KEY holds one key or a comma-separated list."""
    raw = os.getenv("GEMINI_API_KEY", "")
    return [k.strip() for k in raw.split(",") if k.strip()]


def _post_to_gemini(payload: dict, timeout: int):
    """POST to Gemini, rotating (key, model) pairs past any 429."""
    keys = _gemini_keys()
    if not keys:
        raise ValueError("GEMINI_API_KEY not set")

    response = None
    for api_key in keys:
        for model in _MODELS:
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={api_key}"
            )
            print(f"Sending image to Gemini Vision API ({model}, key ...{api_key[-4:]})...")
            response = requests.post(url, json=payload, timeout=timeout)
            if response.status_code == 429:
                print(f"[WARN] {model} quota exceeded for key ...{api_key[-4:]} — trying next...")
                continue
            return response
    return response


def _load_image_b64(image_path: str):
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
    return image_data, mime


def _parse_json_response(raw: str) -> Dict:
    """Parse the model's JSON, stripping code fences and salvaging partials."""
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
    json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass

    print(f"[WARN] JSON parse failed, salvaging fields from partial output:\n{raw}")
    data = {}
    for field in ("unit", "name", "supplier", "parcel_type"):
        m = re.search(rf'"{field}"\s*:\s*"([^"]*)"', raw)
        if m:
            data[field] = m.group(1)
    if not data:
        raise Exception(f"No valid JSON in Gemini output:\n{raw}")
    return data


def extract_with_gemini(image_path: str, building: str = "g2") -> Dict:
    """Primary extraction. responseSchema constrains the output to four fields."""
    if not _gemini_keys():
        raise ValueError("GEMINI_API_KEY not set")

    image_data, mime = _load_image_b64(image_path)

    payload = {
        "contents": [{
            "parts": [
                {"text": _build_main_prompt(building)},
                {"inline_data": {"mime_type": mime, "data": image_data}},
            ]
        }],
        "generationConfig": {
            "temperature": 0,
            "topP": 1,
            "topK": 1,
            "maxOutputTokens": 1024,
            "responseMimeType": "application/json",
            "responseSchema": _RESPONSE_SCHEMA,
        },
    }

    response = _post_to_gemini(payload, timeout=45)
    if response is None or response.status_code != 200:
        code = response.status_code if response is not None else "N/A"
        body = response.text if response is not None else "no response"
        raise Exception(f"Gemini API error {code}: {body}")

    result = response.json()
    if not result.get("candidates"):
        raise Exception("No candidates in Gemini response")

    raw = result["candidates"][0]["content"]["parts"][0].get("text", "")
    data = _parse_json_response(raw)
    return _normalize(data, image_path)


def _normalize(data: Dict, image_path: str) -> Dict:
    """Validate and clean up the extracted fields."""
    # Unit
    unit_raw = str(data.get("unit", "")).strip().upper()
    unit_match = re.search(r"\b(\d{1,5}[A-Z]?)\b", unit_raw)
    unit_candidate = unit_match.group(1) if unit_match else "UNKNOWN"

    # Civic numbers and anything under 100 are not units
    if _is_civic_number(unit_candidate):
        unit_candidate = "UNKNOWN"
    elif unit_candidate.isdigit() and int(unit_candidate) < 100:
        unit_candidate = "UNKNOWN"

    # A bare 5-digit run is a postal or zip code
    if re.fullmatch(r"\d{5}", unit_candidate):
        unit_candidate = "UNKNOWN"

    data["unit"] = unit_candidate

    # Name
    name = str(data.get("name", "")).strip()
    data["name"] = name.title() if name and name.upper() != "UNKNOWN" else "UNKNOWN"

    # Supplier
    supplier = str(data.get("supplier", "OTHER")).strip().upper()
    data["supplier"] = supplier if supplier else "OTHER"

    # Parcel type
    data["parcel_type"] = str(data.get("parcel_type", "")).strip().upper() or guess_parcel_type(image_path)

    return data


def _retry_focused(image_path: str, current: Dict, building: str = "g2") -> Dict:
    """Second, narrower call when unit or name came back UNKNOWN."""
    if current.get("unit") != "UNKNOWN" and current.get("name") != "UNKNOWN":
        return current

    if not _gemini_keys():
        return current

    image_data, mime = _load_image_b64(image_path)

    payload = {
        "contents": [{
            "parts": [
                {"text": _build_focused_prompt(building)},
                {"inline_data": {"mime_type": mime, "data": image_data}},
            ]
        }],
        "generationConfig": {
            "temperature": 0,
            "topP": 1,
            "topK": 1,
            "maxOutputTokens": 512,
            "responseMimeType": "application/json",
            "responseSchema": _FOCUSED_RESPONSE_SCHEMA,
        },
    }

    try:
        resp = _post_to_gemini(payload, timeout=30)
        if resp is None or resp.status_code != 200:
            return current
        result = resp.json()
        if not result.get("candidates"):
            return current
        raw = result["candidates"][0]["content"]["parts"][0].get("text", "")
        retry_data = _parse_json_response(raw)

        if current.get("unit") == "UNKNOWN":
            unit_raw = str(retry_data.get("unit", "")).strip().upper()
            m = re.search(r"\b(\d{1,5}[A-Z]?)\b", unit_raw)
            if m and not _is_civic_number(m.group(1)):
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


def extract_data(image_path: str, building: str = "g2") -> Dict:
    """Gemini first, then a focused retry, then local OCR.

    `building` ('g1' or 'g2') supplies the civic address so the model can
    pinpoint the unit instead of guessing between the numbers on the label.
    """
    print(f"\n{'='*60}")
    print(f"ANALYZING: {os.path.basename(image_path)} (building={building})")
    print(f"{'='*60}\n")

    try:
        result = extract_with_gemini(image_path, building)
    except Exception as e:
        print(f"[WARN] Gemini failed: {e}\nUsing fallback OCR...")
        result = fallback_regex_ocr(image_path, building)

    # Second pass for anything still UNKNOWN
    result = _retry_focused(image_path, result, building)

    # Last resort: fill remaining gaps from local OCR
    for key in ["unit", "name", "supplier", "parcel_type"]:
        if not result.get(key) or result[key] == "UNKNOWN":
            print(f"[WARN] {key} still unknown, trying OCR fallback...")
            backup = fallback_regex_ocr(image_path, building)
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


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python ocr_utils.py <image_path> [building: g1|g2]")
        sys.exit(1)

    path = sys.argv[1]
    bld = sys.argv[2].lower() if len(sys.argv) > 2 else "g2"
    if bld not in ("g1", "g2"):
        bld = "g2"
    if not os.path.exists(path):
        print(f"File not found: {path}")
        sys.exit(1)

    result = extract_data(path, bld)
    print("\nJSON OUTPUT:")
    print(json.dumps(result, indent=2))
