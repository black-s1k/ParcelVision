"""
app2.py - ParcelVision with Remote 1Valet Control
(HTTP Version for NGROK — multi-building G1/G2)
"""

from flask import Flask, request, jsonify, render_template
from flask_socketio import SocketIO

import os
import sys
import inspect
import traceback
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
from sheet_utils import append_row, connect_to_sheet

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
socketio = SocketIO(app, cors_allowed_origins="https://my.1valetbas.com", async_mode="threading")

# Accept WebSocket connections from both 1Valet portals
CORS_ORIGINS = [
    os.getenv("VALET_CORS_ORIGIN",    "https://my.1valetbas.com"),
    os.getenv("G1_VALET_CORS_ORIGIN", ""),
]
_ws_origins = [o for o in CORS_ORIGINS if o]
socketio = SocketIO(app, cors_allowed_origins=_ws_origins, async_mode="threading")

# ── Per-building queues ────────────────────────────────────
pending_units_queue: dict = {"g1": [], "g2": []}

# Job results store for async upload processing
job_results: dict = {}

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ── CORS (HTTP endpoints — handle both 1Valet origins) ──────────────────
def _allowed_origin(origin: str) -> bool:
    return origin in CORS_ORIGINS

@app.after_request
def apply_cors(response):
    origin = request.headers.get("Origin", "")
    if _allowed_origin(origin):
        response.headers["Access-Control-Allow-Origin"]  = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, ngrok-skip-browser-warning"
        response.headers["Access-Control-Max-Age"]       = "600"
    return response

@app.route("/valet/<path:subpath>", methods=["OPTIONS"])
def valet_preflight(subpath):
    return "", 204


# ── SocketIO: building-specific rooms ─────────────────────────────
@socketio.on("join_building")
def on_join_building(data):
    building = data.get("building", "g2")
    if building in ("g1", "g2"):
        join_room(building)
        print(f"[SocketIO] Client joined room: {building}")


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def home():
    try:
        return render_template("index.html")
    except Exception as e:
        return f"<h3 style='color:red'>Template error: {e}</h3>", 500


@app.route("/upload", methods=["POST"])
def upload_parcel():
    """
    Saves image and returns job_id immediately (HTTP 202).
    Processing runs in background; phone polls /result/<job_id>.
    """
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file part"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "No selected file"}), 400

        building = request.form.get("building", "g2").lower()
        if building not in ("g1", "g2"):
            building = "g2"

        job_id    = uuid.uuid4().hex
        temp_path = os.path.join(UPLOAD_FOLDER, f"tmp_{job_id}.jpg")
        file.save(temp_path)

        job_results[job_id] = {"status": "processing"}

        def process(job_id, temp_path, building):
            try:
                print(f"\n[{job_id}] OCR start (building={building})")
                result = analyze_parcel(temp_path)
                if isinstance(result, list):
                    result = result[0] if result else {}

                unit        = str(result.get("unit",        "UNKNOWN")).strip().upper()
                name        = str(result.get("name",        "UNKNOWN")).strip().upper()
                supplier    = str(result.get("supplier",    "OTHER")).strip().upper()
                parcel_type = str(result.get("parcel_type", "BROWN BOX")).strip().upper()

                print(f"[{job_id}] Unit={unit} Name={name} Supplier={supplier}")

                timestamp_readable = datetime.now().strftime("%m/%d/%Y %H:%M:%S")
                timestamp_safe     = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

                append_row(
                    [timestamp_readable, unit, name, supplier, parcel_type, "FALSE", ""],
                    building=building,
                )
                print(f"[{job_id}] Sheets written ({building})")

                safe_name  = f"{timestamp_safe}_{building}_{unit}_{name}_{supplier}_{parcel_type}.jpg"
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
                    pending_units_queue[building].append({
                        "unit": unit, "name": name,
                        "supplier": supplier, "parcel_type": parcel_type,
                        "timestamp": timestamp_readable,
                    })
                    valet_status = "queued"
                    print(f"[{job_id}] Queued for {building}. Queue size: {len(pending_units_queue[building])}")

                job_results[job_id] = {
                    "status": "success",
                    "message": "Parcel processed successfully",
                    "image_saved_as": safe_name,
                    "data": {"unit": unit, "name": name,
                             "supplier": supplier, "parcel_type": parcel_type},
                    "sheets_status": "success",
                    "valet_status": valet_status,
                    "alert": alert_message,
                    "building": building,
                }
                print(f"[{job_id}] Done")

            except Exception as e:
                traceback.print_exc()  
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
                job_results[job_id] = {"status": "error", "error": str(e)}

        threading.Thread(target=process, args=(job_id, temp_path, building), daemon=True).start()
        return jsonify({"status": "processing", "job_id": job_id}), 202

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/result/<job_id>", methods=["GET"])
def get_job_result(job_id):
    result = job_results.get(job_id)
    if result is None:
        return jsonify({"status": "not_found"}), 404
    if result.get("status") in ("success", "error"):
        job_results.pop(job_id, None)
    return jsonify(result)


@app.route("/valet/pending", methods=["GET"])
def get_pending_units():
    building = request.args.get("building", "g2").lower()
    if building not in ("g1", "g2"):
        building = "g2"
    q = pending_units_queue[building]
    if not q:
        return jsonify({"status": "empty", "units": []})
    return jsonify({"status": "pending", "count": len(q), "units": q.copy()})


@app.route("/valet/complete", methods=["POST"])
def mark_unit_complete():
    data     = request.get_json()
    unit     = data.get("unit")
    success  = data.get("success", False)
    building = data.get("building", "g2").lower()
    if building not in ("g1", "g2"):
        building = "g2"
    if success:
        pending_units_queue[building] = [
            u for u in pending_units_queue[building] if u["unit"] != unit
        ]
        remaining = len(pending_units_queue[building])
        print(f"[{building}] Unit {unit} complete. Remaining: {remaining}")
        return jsonify({
            "status": "success",
            "message": f"Unit {unit} removed from queue",
            "remaining": remaining,
        })
    print(f"[{building}] Unit {unit} failed to add to 1Valet")
    return jsonify({"status": "error", "message": "Failed to add unit"}), 400


@app.route("/valet/queue-status", methods=["GET"])
def queue_status():
    building = request.args.get("building", "g2").lower()
    if building not in ("g1", "g2"):
        building = "g2"
    q = pending_units_queue[building]
    return jsonify({"building": building, "queue_size": len(q), "pending_units": [u["unit"] for u in q]})


@app.route("/valet/clear-queue", methods=["POST"])
def clear_queue():
    data     = request.get_json(silent=True) or {}
    building = data.get("building", request.args.get("building", "g2")).lower()
    if building not in ("g1", "g2"):
        building = "g2"
    count = len(pending_units_queue[building])
    pending_units_queue[building] = []
    return jsonify({"status": "success", "message": f"Cleared {count} units from {building} queue"})


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("ParcelVision - Remote 1Valet Control (G1 + G2)")
    print("=" * 60)
    print("\nServer starting on http://0.0.0.0:5002\n")
    try:
        import socket
        local_ip = socket.gethostbyname(socket.gethostname())
        print(f"Local IP: {local_ip}")
        print(f"Phone access: http://{local_ip}:5002\n")
    except Exception:
        pass
    socketio.run(app, host="0.0.0.0", port=5002, debug=True, use_reloader=False)
