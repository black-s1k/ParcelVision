/**
 * Google Apps Script — ParcelVision Release Trigger
 *
 * Bind this script to your Google Sheet (Extensions → Apps Script).
 * Set up an installable onEdit trigger (Triggers → Add Trigger → onEdit).
 *
 * When the RELEASED checkbox in column F is ticked TRUE, this script
 * POSTs the unit number (column B) to Flask /valet/release.
 * The 1Valet browser script then picks it up and asks for confirmation
 * before clicking "Mark as retrieved" in the smart locker UI.
 *
 * SERVER_URL is auto-updated by start.sh each time the tunnel restarts.
 */

const SERVER_URL = "https://YOUR_TUNNEL_URL";

// Column indexes (1-based)
const COL_UNIT     = 2; // B — Unit number
const COL_RELEASED = 6; // F — RELEASED checkbox

function onEdit(e) {
  if (!e) return; // guard against manual runs

  const range = e.range;

  // Only react to column F
  if (range.getColumn() !== COL_RELEASED) return;

  // Only when checkbox becomes checked (TRUE)
  const newValue = e.value;
  if (newValue !== "TRUE" && newValue !== true) return;

  const row = range.getRow();
  if (row <= 1) return; // skip header row

  const sheet = e.source.getActiveSheet();
  const unit  = String(sheet.getRange(row, COL_UNIT).getValue()).trim().toUpperCase();

  if (!unit || unit === "UNKNOWN" || unit === "") {
    Logger.log(`Row ${row}: no valid unit — skipping release.`);
    return;
  }

  if (SERVER_URL.includes("YOUR_TUNNEL_URL")) {
    Logger.log("ERROR: SERVER_URL not set. Re-run start.sh to update it.");
    return;
  }

  try {
    const options = {
      method:          "post",
      contentType:     "application/json",
      payload:         JSON.stringify({ unit }),
      muteHttpExceptions: true
    };
    const response = UrlFetchApp.fetch(SERVER_URL + "/valet/release", options);
    Logger.log(`Release queued for unit ${unit} — server response: ${response.getContentText()}`);
  } catch (err) {
    Logger.log(`Error queuing release for unit ${unit}: ${err.message}`);
  }
}
