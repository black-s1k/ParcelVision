"""Thin wrapper around the OCR pipeline."""

from ocr_utils import extract_data


def analyze_parcel(image_path, building="g2"):
    """Extract label information from a parcel image.

    Args:
        image_path (str): Path to the parcel image
        building (str): 'g1' or 'g2'. Supplies the civic address the parcel
            was scanned at, which is what pins down the unit number.

    Returns:
        dict: unit, name, supplier, parcel_type
    """
    return extract_data(image_path, building)
