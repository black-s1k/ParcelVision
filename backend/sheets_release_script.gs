/**
 * Release trigger for the ParcelVision sheet.
 *
 * Bind to the sheet under Extensions > Apps Script, then add an installable
 * onEdit trigger. Ticking the RELEASED checkbox in column F posts that row's
 * unit number to Flask, and the 1Valet script marks the parcel retrieved.
 */

const SERVER_URL = "https://YOUR_TUNNEL_URL";

// Column indexes, 1-based
const COL_UNIT     = 2; // B, unit number
const COL_RELEASED = 6; // F, RELEASED checkbox

function onEdit(e) {
  if (!e) return; // manual run

  const range = e.range;

  if (range.getColumn() !== COL_RELEASED) return;

  // Only fire when the box goes from unchecked to checked
  const newValue = e.value;
  if (newValue !== "TRUE" && newValue !== true) return;

  const row = range.getRow();
  if (row <= 1) return; // header

  const sheet = e.source.getActiveSheet();
  const unit  = String(sheet.getRange(row, COL_UNIT).getValue()).trim().toUpperCase();

  if (!unit || unit === "UNKNOWN" || unit === "") {
    Logger.log(`Row ${row}: no valid unit, skipping release.`);
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
    Logger.log(`Release queued for unit ${unit}, server response: ${response.getContentText()}`);
  } catch (err) {
    Logger.log(`Error queuing release for unit ${unit}: ${err.message}`);
  }
}
