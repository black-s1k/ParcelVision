# ParcelVision — Multi-Property Architecture

How ParcelVision serves more than one building, what it costs to onboard the
next one, and where the seams are if a property runs something other than
1Valet.

The short version: **the vendor integration was solved once, not once per
building.** Onboarding is a configuration problem, not an engineering problem —
and this document is honest about the gap between that being true in principle
and true in the code.

---

## 1. The integration model

1VALET is a smart-building operating system — intercom, access control,
resident app, and property-management portal. It exposes no public API, no SDK,
and no integration path for third parties.

The integration is therefore built against the only surface that exists: the
web application itself. A listener script is injected into a logged-in 1Valet
tab via the Chrome DevTools Protocol, polls this server for pending parcels,
and drives the "Add Delivery" flow using DOM introspection and synthetic event
dispatch.

**The critical property: 1Valet's UI is identical across every building it
serves.** It is one SaaS application with one front end. The selectors, the
typing sequence, the dropdown match, and the re-focus behaviour do not vary by
property. That means the reverse-engineering effort is a fixed, one-time cost —
it does not repeat per building.

What varies per property is configuration, not behaviour.

---

## 2. What actually varies per property

| # | Value | Where it comes from | Used for |
|---|---|---|---|
| 1 | Building slug (`g1`, `g2`, …) | You choose it | Queue key, SocketIO room, routing |
| 2 | Display name and civic number | The building's street address | OCR context — lets the model eliminate the civic number when picking the unit |
| 3 | Two or three unit examples | Real address lines from that building's labels | Few-shot examples in the extraction prompt |
| 4 | Google Sheet ID and worksheet name | The sheet's URL | Where parcel rows are appended |
| 5 | Service-account credentials file | Google Cloud console; share the sheet with the service-account email | Sheets authentication |
| 6 | 1Valet CORS origin | The portal domain — usually shared across properties | Browser script fetch permission |

Plus one operational item that is not configuration: **a logged-in 1Valet
browser session on a machine at (or reachable from) the property.** This is the
one piece that cannot move to the cloud — see §5.

That is five values and a browser tab. Nothing about the DOM, the event
sequence, or the extraction pipeline changes.

---

## 3. Current onboarding cost — the honest number

Conceptually, adding a property is the six values above. In the code as it
stands today, it is edits in six places:

| File | What must change |
|---|---|
| `backend/ocr_utils.py` | New entry in `_BUILDING_INFO` (name, civic, unit examples) |
| `backend/app2.py` | Add the slug to `pending_units_queue` and `release_queue`; extend `valid_building()`; extend the poller startup loop |
| `backend/sheet_utils.py` | New prefixed env reads plus an entry in `_BUILDING_CONFIG` |
| `backend/.env` | Three new prefixed variables (`G3_SHEET_ID`, …) |
| `backend/smartlockerscript*.txt` | Copy an existing script and edit the building constant |
| `start.sh` | Add the new script file to the URL/token stamping loop |

The `G1_` / `G2_` environment-variable prefix convention is the tell. Every new
property adds three more prefixed variables and at least one new conditional
branch. It works at two properties. It does not survive four.

**This is the gap: the architecture is single-integration, but the
configuration is still expressed as code.**

---

## 4. Target state — configuration as data

One buildings map is the source of truth. A JSON file today; a `buildings`
table once the system moves to a managed database.

```json
{
  "g2": {
    "name": "Galleria 2",
    "address": "10 Graphophone Grove, Toronto",
    "civic": "10",
    "unit_examples": [
      ["504-10 GRAPHOPHONE GROVE", "504"],
      ["2406 10 GRAPHOPHONE GROVE", "2406"]
    ],
    "sheet_id": "…",
    "worksheet": "PACKAGES NEW",
    "credentials": "credentials.json",
    "valet_origin": "https://my.1valetbas.com"
  }
}
```

Everything else derives from it rather than restating it:

```python
pending_units_queue = {b: [] for b in BUILDINGS}
release_queue       = {b: [] for b in BUILDINGS}

for bld in BUILDINGS:
    threading.Thread(target=_poll_sheet_releases, args=(bld,), daemon=True).start()

def valid_building(v: str) -> str:
    v = (v or DEFAULT_BUILDING).lower()
    return v if v in BUILDINGS else DEFAULT_BUILDING
```

`_BUILDING_INFO` (OCR context), `_BUILDING_CONFIG` (Sheets), and the CORS
origin allowlist all read from the same map.

**The browser script collapses too.** `smartlockerscript.txt` and
`smartlockerscript_g1.txt` are currently 95% identical — the DOM logic is
byte-for-byte the same, and they differ only in a building constant, two
request parameters, and console strings. There should be one script, with
`BUILDING` injected at load time exactly as `SERVER_URL` and `API_TOKEN`
already are by `inject_tab.py`.

> **A bug this duplication already caused:** the G2 copy of the script never
> sends the `building` parameter at all. It routes correctly only because
> `valid_building()` happens to default to `"g2"`. Change that default, or add
> a third property, and it silently writes into the wrong queue.

After the refactor, onboarding is:

1. Add one object to the buildings map
2. Drop in the service-account credentials file
3. Share the sheet with the service-account email
4. Open a logged-in 1Valet tab and run the injector

**Zero code changes.**

---

## 5. What cannot be centralised

The browser agent must run where a logged-in 1Valet session can exist. No
architecture removes that constraint — it is a property of integrating with a
vendor that has no API.

This produces the system's real shape: a **central control plane** (intake API,
extraction pipeline, storage, queues) with a **per-property edge agent** that
holds the vendor session and reaches back to the control plane over
authenticated HTTP.

That split is not a workaround. It is the correct architecture for this class
of integration, and it is what makes the system portable to properties whose
systems can never be reached directly.

---

## 6. If a property does not run 1Valet

Everything above assumes 1Valet. If a property runs a different platform, the
core is unaffected — intake, extraction, validation, queueing, storage, and
notification never reference the vendor.

The seam is deliberately thin:

```python
class PropertySystemAdapter(Protocol):
    def validate_unit(self, unit: str) -> UnitCheck: ...
    def register_delivery(self, parcel: Parcel) -> DeliveryResult: ...
    def mark_retrieved(self, unit: str) -> None: ...
```

The current browser-driven implementation satisfies all three through the UI —
`validate_unit` via the suite-search dropdown, `register_delivery` via the Add
Delivery flow, `mark_retrieved` via the release path. A vendor with a real API
would implement the same three methods over HTTP.

One interface, one implementation, no plugin registry. The abstraction is kept
minimal on purpose: with a single vendor, anything more elaborate is
speculative architecture.

**Graceful fallback matters here.** With no adapter configured, the system still
performs intake, extraction, logging, and resident notification — it simply
does not auto-enter into the vendor portal. A new property gets most of the
value on day one with no integration work at all, and the connector becomes an
upgrade rather than a prerequisite.

---

## 7. Onboarding runbook

Once §4 is implemented:

```bash
# 1. Create the service account and download its key
#    → Google Cloud console → IAM → Service Accounts → Keys
mv ~/Downloads/<key>.json backend/credentials_g3.json

# 2. Share the property's Google Sheet with the service-account email
#    (Editor access) — copy the address from the key file's client_email

# 3. Add the property to the buildings map
$EDITOR backend/buildings.json

# 4. Restart the server; the poller thread for the new property starts itself
./start.sh

# 5. Open a logged-in 1Valet tab for the property, then inject the agent
python3 backend/inject_tab.py "$SERVER_URL"
```

Verify: scan one test label from the phone UI, confirm the row lands in the
correct sheet, and confirm the unit appears in the 1Valet Add Delivery flow.

---

## 8. Scaling limits that remain

Config-as-data removes the code-fork problem. It does not remove these:

| Limit | Bites at | Cause |
|---|---|---|
| One browser session per property | Every property | Inherent to a vendor with no API |
| In-memory queues | First restart | Per-process state; see `SYSTEMDESIGN.md` |
| Google Sheets as system of record | ~5 concurrent writers | `col_values(1)` makes appends O(rows) |
| Sheet-poller threads | ~20 properties | One thread per property, each polling every 5s |
| Single machine and tunnel | Machine sleeps | Single point of failure across all properties |

The poller ceiling is the one that arrives first as properties are added. Its
fix is the same as everything else in `SYSTEMDESIGN.md` Part 2 — replace
polling with events, so property count stops multiplying background work.

---

*See also: `SYSTEMDESIGN.md` for the scaling analysis and cloud roadmap,
`SECURITY.md` for the threat model and the per-property isolation guarantees.*
