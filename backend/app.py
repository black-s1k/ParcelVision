"""Single-building ParcelVision server. Superseded by app2.py."""

from flask import Flask, request, jsonify, render_template
import os
import sys
import traceback
from datetime import datetime

# Import local modules from this directory
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from vision_utils import analyze_parcel
from sheet_utils import append_row

# Resolve paths relative to this file
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = (
    os.path.join(BASE_DIR, "backend", "templates")
    if os.path.basename(BASE_DIR) != "backend"
    else os.path.join(BASE_DIR, "templates")
)

app = Flask(__name__, template_folder=TEMPLATE_DIR)

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@app.route("/")
def home():
    """Serve the camera upload UI."""
    try:
        return render_template("index.html")
    except Exception as e:
        return f"<h3 style='color:red'>Template not found or failed to load: {e}</h3>", 500


@app.route("/upload", methods=["POST"])
def upload_parcel():
    """Run OCR on an uploaded image, log it to Sheets and keep a copy."""
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file part"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "No selected file"}), 400

        temp_path = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(temp_path)

        result = analyze_parcel(temp_path)
        if isinstance(result, list):
            result = result[0] if result else {}

        unit        = result.get("unit",        "UNKNOWN")
        name        = result.get("name",        "UNKNOWN")
        supplier    = result.get("supplier",    "UNKNOWN")
        parcel_type = result.get("parcel_type", "UNKNOWN")

        print(f"Unit={unit} Name={name} Supplier={supplier} Type={parcel_type}")

        timestamp_readable = datetime.now().strftime("%m/%d/%Y %H:%M:%S")
        timestamp_safe     = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        safe_name = (
            f"{timestamp_safe}_{unit}_{name}_{supplier}_{parcel_type}.jpg"
        ).replace(" ", "_").replace("/", "-")

        final_path = os.path.join(UPLOAD_FOLDER, safe_name)
        os.rename(temp_path, final_path)

        append_row([timestamp_readable, unit, name, supplier, parcel_type])

        return jsonify({
            "status": "success",
            "message": "Parcel logged successfully",
            "image_saved_as": safe_name,
            "data": result
        }), 200

    except Exception as e:
        print("ERROR:", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
