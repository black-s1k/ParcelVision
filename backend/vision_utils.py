"""
Vision utilities for parcel analysis.
Simply delegates to the Gemini-powered OCR.
"""

from ocr_utils import extract_data


def analyze_parcel(image_path, building="g2"):
    """
    Analyzes a parcel image and extracts label information.

    Args:
        image_path (str): Path to the parcel image
        building (str): 'g1' or 'g2' — tells the OCR which civic address
            this parcel was scanned at, so it can pinpoint the unit number.

    Returns:
        dict: Contains unit, name, supplier, parcel_type
    """
    return extract_data(image_path, building)
