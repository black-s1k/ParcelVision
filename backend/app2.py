"""
app2.py - ParcelVision with Remote 1Valet Control
(HTTP Version for NGROK)
"""

from flask import Flask, request, jsonify, render_template

import os
import sys
import inspect
import traceback
import json
import threading
import uuid
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import local modules
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from vision_utils import analyze_parcel
from sheet_utils import append_row

print("Loaded modules:")
print(f"  - vision_utils from: {inspect.getfile(analyze_parcel)}")
print(f"  - sheet_utils from: {inspect.getfile(append_row)}")

# Setup Flask app
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = (
    os.path.join(BASE_DIR, "backend", "templates")
    if os.path.basename(BASE_DIR) != "backend"
    else os.path.join(BASE_DIR, "templates")
)

app = Flask(__name__, template_folder=TEMPLATE_DIR)

# Allow the 1Valet portal to call /valet/* from the browser
CORS_ORIGIN = "https://my.1valetbas.com"

@app.after_request
def apply_cors(response):
    origin = request.headers.get("Origin", "")
    if origin == CORS_ORIGIN:
        response.headers["Access-Control-Allow-Origin"]  = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, ngrok-skip-browser-warning"
        response.headers["Access-Control-Max-Age"]       = "600"
    return response

@app.route("/valet/<path:subpath>", methods=["OPTIONS"])
def valet_preflight(subpath):
    return "", 204

# ===============================

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Queue to store units pending 1Valet addition
pending_units_queue = []

# Queue to store units pending retrieval (RELEASED checkbox ticked in Sheets)
release_queue = []

# Job results store for async upload processing
job_results = {}


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def home():
    """Serve the camera upload UI"""
    try:
        return render_template("index.html")
    except Exception as e:
        return f"<h3 style='color:red'>Template error: {e}</h3>", 500


@app.route("/upload", methods=["POST"])
def upload_parcel():
    """
    Saves the image and immediately returns a job_id.
    Processing (OCR + Sheets + queue) runs in a background thread.
    The phone polls /result/<job_id> for the outcome.
    """
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file part"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "No selected file"}), 400

        # Save to a unique temp path so concurrent uploads don't collide
        job_id = uuid.uuid4().hex
        temp_path = os.path.join(UPLOAD_FOLDER, f"tmp_{job_id}.jpg")
        file.save(temp_path)

        job_results[job_id] = {"status": "processing"}

        def process(job_id, temp_path):
            try:
                print(f"\n[{job_id}] OCR start")
                result = analyze_parcel(temp_path)
                if isinstance(result, list):
                    result = result[0] if result else {}

                unit         = str(result.get("unit",        "UNKNOWN")).strip().upper()
                name         = str(result.get("name",        "UNKNOWN")).strip().upper()
                supplier     = str(result.get("supplier",    "OTHER")).strip().upper()
                parcel_type  = str(result.get("parcel_type", "BROWN BOX")).strip().upper()

                print(f"[{job_id}] Unit={unit} Name={name} Supplier={supplier}")

                timestamp_readable = datetime.now().strftime("%m/%d/%Y %H:%M:%S")
                timestamp_safe     = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

                append_row([timestamp_readable, unit, name, supplier, parcel_type, "", ""])
                print(f"[{job_id}] Sheets written")

                safe_name  = f"{timestamp_safe}_{unit}_{name}_{supplier}_{parcel_type}.jpg"
                safe_name  = safe_name.replace(" ", "_").replace("/", "-")
                final_path = os.path.join(UPLOAD_FOLDER, safe_name)
                os.rename(temp_path, final_path)

                valet_status  = "skipped"
                alert_message = None

                if not unit or unit == "UNKNOWN":
                    valet_status  = "error"
                    alert_message = f"UNIT NOT RECOGNIZED — parcel for: {name}"
                    print(f"[{job_id}] {alert_message}")
                else:
                    pending_units_queue.append({
                        "unit": unit, "name": name,
                        "supplier": supplier, "parcel_type": parcel_type,
                        "timestamp": timestamp_readable
                    })
                    valet_status = "queued"
                    print(f"[{job_id}] Queued. Queue size: {len(pending_units_queue)}")

                job_results[job_id] = {
                    "status": "success",
                    "message": "Parcel processed successfully",
                    "image_saved_as": safe_name,
                    "data": {"unit": unit, "name": name,
                             "supplier": supplier, "parcel_type": parcel_type},
                    "sheets_status": "success",
                    "valet_status": valet_status,
                    "alert": alert_message
                }
                print(f"[{job_id}] Done")

            except Exception as e:
                traceback.print_exc()
                if os.path.exists(temp_path):
                    try: os.remove(temp_path)
                    except: pass
                job_results[job_id] = {"status": "error", "error": str(e)}

        threading.Thread(target=process, args=(job_id, temp_path), daemon=True).start()
        return jsonify({"status": "processing", "job_id": job_id}), 202

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/result/<job_id>", methods=["GET"])
def get_job_result(job_id):
    """Phone polls this after /upload returns a job_id."""
    result = job_results.get(job_id)
    if result is None:
        return jsonify({"status": "not_found"}), 404
    if result.get("status") in ("success", "error"):
        job_results.pop(job_id, None)
    return jsonify(result)


@app.route("/valet/pending", methods=["GET"])
def get_pending_units():
    global pending_units_queue
    if not pending_units_queue:
        return jsonify({"status": "empty", "units": []})
    units = pending_units_queue.copy()
    return jsonify({"status": "pending", "count": len(units), "units": units})


@app.route("/valet/complete", methods=["POST"])
def mark_unit_complete():
    global pending_units_queue
    data    = request.get_json()
    unit    = data.get("unit")
    success = data.get("success", False)
    if success:
        pending_units_queue = [u for u in pending_units_queue if u["unit"] != unit]
        print(f"Unit {unit} marked complete. Remaining: {len(pending_units_queue)}")
        return jsonify({"status": "success", "message": f"Unit {unit} removed from queue", "remaining": len(pending_units_queue)})
    else:
        print(f"Unit {unit} failed to add to 1Valet")
        return jsonify({"status": "error", "message": "Failed to add unit"}), 400


@app.route("/valet/queue-status", methods=["GET"])
def queue_status():
    return jsonify({"queue_size": len(pending_units_queue), "pending_units": [u["unit"] for u in pending_units_queue]})


@app.route("/valet/clear-queue", methods=["POST"])
def clear_queue():
    global pending_units_queue
    count = len(pending_units_queue)
    pending_units_queue = []
    return jsonify({"status": "success", "message": f"Cleared {count} units from queue"})


@app.route("/valet/release", methods=["POST"])
def queue_release():
    """Google Apps Script calls this when RELEASED checkbox is ticked in Sheets."""
    global release_queue
    data = request.get_json(silent=True) or {}
    unit = str(data.get("unit", "")).strip().upper()
    if not unit:
        return jsonify({"error": "unit required"}), 400
    release_queue.append({"unit": unit, "timestamp": datetime.now().strftime("%m/%d/%Y %H:%M:%S")})
    print(f"Release queued for unit {unit}. Queue size: {len(release_queue)}")
    return jsonify({"status": "queued", "unit": unit})


@app.route("/valet/release-pending", methods=["GET"])
def get_release_pending():
    """Browser script polls this; returns and clears pending releases."""
    global release_queue
    if not release_queue:
        return jsonify({"status": "empty", "units": []})
    units = release_queue.copy()
    release_queue = []
    return jsonify({"status": "pending", "count": len(units), "units": units})


if __name__ == "__main__":
    print("\n" + "="*60)
    print("ParcelVision - Remote 1Valet Control")
    print("="*60)
    print("\nServer starting on http://0.0.0.0:5002\n")
    try:
        import socket
        local_ip = socket.gethostbyname(socket.gethostname())
        print(f"Local IP: {local_ip}")
        print(f"Phone access: http://{local_ip}:5002\n")
    except Exception:
        pass
    app.run(host="0.0.0.0", port=5002, debug=True)
