from flask import Flask, request, jsonify, render_template
import os
import sys
import inspect
import traceback

# Import local modules from this directory
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from vision_utils import analyze_parcel
from sheet_utils import append_row
from datetime import datetime

print("🔍 vision_utils loaded from:", inspect.getfile(analyze_parcel))
print("🔍 sheet_utils loaded from:", inspect.getfile(append_row))

import vision_utils
import ocr_utils
print("🧠 vision_utils module loaded from:", inspect.getfile(vision_utils))
print("🧠 ocr_utils module loaded from:", inspect.getfile(ocr_utils))

# Resolve paths relative to this file
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = (
    os.path.join(BASE_DIR, "backend", "templates")
    if os.path.basename(BASE_DIR) != "backend"
    else os.path.join(BASE_DIR, "templates")
)

print("🧭 BASE_DIR:", BASE_DIR)
print("🧭 TEMPLATE_DIR:", TEMPLATE_DIR)

app = Flask(__name__, template_folder=TEMPLATE_DIR)

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@app.route("/")
def home():
    """Serve the camera upload UI"""
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

        print("DEBUG calling analyze_parcel() ...")
        result = analyze_parcel(temp_path)
        print("DEBUG type:", type(result))
        print("DEBUG result preview:", str(result)[:300])

        # analyze_parcel() sometimes returns a list
        if isinstance(result, list):
            print("⚠️ analyze_parcel() returned a list, taking first element")
            result = result[0] if result else {}

        print("=" * 50)
        print("🔍 DETAILED DEBUG BEFORE safe_name CONSTRUCTION:")
        print(f"  result type: {type(result)}")
        print(f"  result value: {result}")
        print(f"  result is dict: {isinstance(result, dict)}")
        print(f"  result is list: {isinstance(result, list)}")
        
        try:
            unit_val = result.get('unit', 'UNKNOWN')
            print(f"  result.get('unit', 'UNKNOWN') = {unit_val} (type: {type(unit_val)})")
        except Exception as e:
            print(f"  ❌ ERROR getting 'unit': {e}")
            
        try:
            name_val = result.get('name', 'UNKNOWN')
            print(f"  result.get('name', 'UNKNOWN') = {name_val} (type: {type(name_val)})")
        except Exception as e:
            print(f"  ❌ ERROR getting 'name': {e}")
            
        try:
            supplier_val = result.get('supplier', 'UNKNOWN')
            print(f"  result.get('supplier', 'UNKNOWN') = {supplier_val} (type: {type(supplier_val)})")
        except Exception as e:
            print(f"  ❌ ERROR getting 'supplier': {e}")
            
        try:
            parcel_type_val = result.get('parcel_type', 'UNKNOWN')
            print(f"  result.get('parcel_type', 'UNKNOWN') = {parcel_type_val} (type: {type(parcel_type_val)})")
        except Exception as e:
            print(f"  ❌ ERROR getting 'parcel_type': {e}")
        print("=" * 50)

        timestamp_readable = datetime.now().strftime("%m/%d/%Y %H:%M:%S")
        timestamp_safe = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        print("🔍 CONSTRUCTING safe_name...")
        safe_name = (
            f"{timestamp_safe}_"
            f"{result.get('unit','UNKNOWN')}_"
            f"{result.get('name','UNKNOWN')}_"
            f"{result.get('supplier','UNKNOWN')}_"
            f"{result.get('parcel_type','UNKNOWN')}.jpg"
        ).replace(" ", "_").replace("/", "-")
        print(f"🔍 safe_name constructed: {safe_name}")

        final_path = os.path.join(UPLOAD_FOLDER, safe_name)
        os.rename(temp_path, final_path)

        print("=" * 50)
        print("🔍 DETAILED DEBUG BEFORE row CONSTRUCTION:")
        print(f"  timestamp_readable: {timestamp_readable} (type: {type(timestamp_readable)})")

        try:
            row_unit = result.get("unit", "UNKNOWN")
            print(f"  result.get('unit', 'UNKNOWN') = {row_unit} (type: {type(row_unit)})")
        except Exception as e:
            print(f"  ❌ ERROR getting 'unit' for row: {e}")
            
        try:
            row_name = result.get("name", "UNKNOWN")
            print(f"  result.get('name', 'UNKNOWN') = {row_name} (type: {type(row_name)})")
        except Exception as e:
            print(f"  ❌ ERROR getting 'name' for row: {e}")
            
        try:
            row_supplier = result.get("supplier", "UNKNOWN")
            print(f"  result.get('supplier', 'UNKNOWN') = {row_supplier} (type: {type(row_supplier)})")
        except Exception as e:
            print(f"  ❌ ERROR getting 'supplier' for row: {e}")
            
        try:
            row_parcel_type = result.get("parcel_type", "UNKNOWN")
            print(f"  result.get('parcel_type', 'UNKNOWN') = {row_parcel_type} (type: {type(row_parcel_type)})")
        except Exception as e:
            print(f"  ❌ ERROR getting 'parcel_type' for row: {e}")
        print("=" * 50)

        row = [
            timestamp_readable,
            result.get("unit", "UNKNOWN"),
            result.get("name", "UNKNOWN"),
            result.get("supplier", "UNKNOWN"),
            result.get("parcel_type", "UNKNOWN"),
            "",
            "",
        ]
        print(f"🔍 row constructed: {row}")
        append_row(row)
        print("✅ append_row(row) completed successfully")

        return jsonify({
            "status": "success",
            "message": "Parcel logged successfully",
            "image_saved_as": safe_name,
            "data": result
        }), 200

    except Exception as e:
        print("❌ ERROR:", e)
        print("🔍 FULL STACK TRACE:")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print("🚀 Serving from:", os.path.abspath(__file__))
    print("🔍 Template folder:", TEMPLATE_DIR)
    app.run(host="0.0.0.0", port=5001, debug=True)