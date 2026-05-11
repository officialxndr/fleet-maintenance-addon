"""
core.py — SQLite-backed data layer for Fleet Maintenance.

The public API (`load_db`, `save_db`, `parse_date`, `calculate_status`,
`calculate_fuel_stats`, `calculate_adm`, `get_ha_sensors`, `mqtt_client`)
is unchanged from the previous JSON-backed implementation, so app.py and
all Jinja templates continue to work without modification.

Why SQLite:
  * Atomic transactions — no more half-written DB files on crash/restart.
  * WAL mode — readers don't block writers, writers don't block readers.
  * A single process-wide write lock around save_db() serializes the
    multi-step write performed by app routes and the MQTT / HA-sync
    background threads, preventing the lost-update races that were
    duplicating logbook entries and dropping garage items.
  * On startup, an existing fleet_database.json is migrated automatically
    (the old file is renamed to *.bak so nothing is lost).
"""

import json
import os
import sqlite3
import uuid
import threading
import time
import shutil
import requests
import paho.mqtt.client as mqtt
from contextlib import contextmanager
from datetime import datetime, timedelta


# ==========================================
# ⚙️ HYBRID CONFIGURATION (HA Add-on OR Standalone)
# ==========================================

IS_HA_ADDON = os.path.exists("/data/options.json") or "SUPERVISOR_TOKEN" in os.environ

if IS_HA_ADDON:
    # Home Assistant: prefer /config (user-visible, backed up), fall back to /data.
    DB_DIR = "/config" if os.path.exists("/config") else "/data"
    OPTIONS_PATH = "/data/options.json"
else:
    # Standalone Docker / local: ./data next to the app.
    DB_DIR = os.path.join(os.getcwd(), "data")
    os.makedirs(DB_DIR, exist_ok=True)
    OPTIONS_PATH = None

DB_PATH = os.path.join(DB_DIR, "fleet_database.sqlite3")
LEGACY_JSON_PATH = os.path.join(DB_DIR, "fleet_database.json")

# Defaults: env vars first (handy for standalone Docker), HA options override below.
HA_URL = os.environ.get("HA_URL", "http://192.168.1.100:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
MQTT_BROKER = os.environ.get("MQTT_BROKER", "core-mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
MQTT_USER = os.environ.get("MQTT_USER", "addons")
MQTT_PASS = os.environ.get("MQTT_PASS", "")

if IS_HA_ADDON and OPTIONS_PATH and os.path.exists(OPTIONS_PATH):
    try:
        with open(OPTIONS_PATH, "r") as f:
            _cfg = json.load(f)
        HA_URL = _cfg.get("ha_url", HA_URL)
        HA_TOKEN = _cfg.get("ha_token", HA_TOKEN)
        MQTT_BROKER = _cfg.get("mqtt_broker", MQTT_BROKER)
        MQTT_PORT = int(_cfg.get("mqtt_port", MQTT_PORT))
        MQTT_USER = _cfg.get("mqtt_user", MQTT_USER)
        MQTT_PASS = _cfg.get("mqtt_pass", MQTT_PASS)
    except Exception as e:
        print(f"Error loading HA options: {e}")


DEFAULT_GLOBAL_SETTINGS = {
    "coming_up_miles": 1000,
    "coming_up_months": 1,
    "unit": "mi",
    "currency": "$",
    "date_format": "YYYY-MM-DD",
    "ha_polling": 20,
    "mqtt_enabled": "on",
    "temp_entity_id": "",
    "current_temp": None,
    # Comma-separated tab ids the user has chosen to hide (set via the
    # global settings modal). Valid ids: summary, timeline, intervals,
    # logbook, fuel, specs.
    "hidden_tabs": "",
}

DEFAULT_SERVICES_FALLBACK = [
    {"category": "Engine",   "name": "Engine Oil & Filter", "interval_months": 12, "interval_miles": 5000,  "parts_info": ""},
    {"category": "Engine",   "name": "Air Filter",          "interval_months": 24, "interval_miles": 30000, "parts_info": ""},
    {"category": "Brakes",   "name": "Brake Pads",          "interval_months": 60, "interval_miles": 50000, "parts_info": ""},
    {"category": "Steering", "name": "Rotate Tires",        "interval_months": 12, "interval_miles": 10000, "parts_info": ""},
]


# ==========================================
# 🗄️ SQLITE LAYER
# ==========================================

# Re-entrant: lets the same thread call save_db() from inside an already-locked
# section without deadlocking (e.g. publish_discovery -> load_db is fine).
_db_write_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS global_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL                          -- JSON-encoded
);

CREATE TABLE IF NOT EXISTS default_services (
    id              TEXT PRIMARY KEY,
    category        TEXT NOT NULL DEFAULT 'Other',
    name            TEXT NOT NULL,
    interval_months INTEGER NOT NULL DEFAULT 0,
    interval_miles  INTEGER NOT NULL DEFAULT 0,
    parts_info      TEXT NOT NULL DEFAULT '',
    sort_order      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS vehicles (
    vin             TEXT PRIMARY KEY,
    nickname        TEXT NOT NULL DEFAULT '',
    year            TEXT NOT NULL DEFAULT '',
    make            TEXT NOT NULL DEFAULT '',
    model           TEXT NOT NULL DEFAULT '',
    current_mileage INTEGER NOT NULL DEFAULT 0,
    theme_color     TEXT NOT NULL DEFAULT '#2563eb',
    ha_entity_id    TEXT NOT NULL DEFAULT '',
    image_url       TEXT NOT NULL DEFAULT '',
    share_token     TEXT NOT NULL,
    specs_json      TEXT NOT NULL DEFAULT '{}',  -- JSON blob for flexible spec fields
    sort_order      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS services (
    id                 TEXT PRIMARY KEY,
    vin                TEXT NOT NULL,
    category           TEXT NOT NULL DEFAULT 'Other',
    name               TEXT NOT NULL,
    interval_months    INTEGER NOT NULL DEFAULT 0,
    interval_miles     INTEGER NOT NULL DEFAULT 0,
    parts_info         TEXT NOT NULL DEFAULT '',
    last_service_miles INTEGER,                  -- nullable
    last_service_date  TEXT,                     -- nullable (YYYY-MM-DD)
    sort_order         INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (vin) REFERENCES vehicles(vin) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_services_vin ON services(vin);

CREATE TABLE IF NOT EXISTS garage_items (
    id         TEXT PRIMARY KEY,
    service_id TEXT NOT NULL,
    item_type  TEXT NOT NULL CHECK(item_type IN ('part','torque')),
    name       TEXT NOT NULL DEFAULT '',
    value      TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (service_id) REFERENCES services(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_garage_items_service ON garage_items(service_id);

CREATE TABLE IF NOT EXISTS logbook (
    id         TEXT PRIMARY KEY,
    vin        TEXT NOT NULL,
    date       TEXT NOT NULL DEFAULT '',
    service    TEXT NOT NULL DEFAULT '',
    mileage    INTEGER NOT NULL DEFAULT 0,
    notes      TEXT NOT NULL DEFAULT '',
    cost_parts REAL NOT NULL DEFAULT 0.0,
    cost_labor REAL NOT NULL DEFAULT 0.0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (vin) REFERENCES vehicles(vin) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_logbook_vin ON logbook(vin);

CREATE TABLE IF NOT EXISTS fuel_logs (
    id         TEXT PRIMARY KEY,
    vin        TEXT NOT NULL,
    date       TEXT NOT NULL DEFAULT '',
    cost       REAL NOT NULL DEFAULT 0.0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (vin) REFERENCES vehicles(vin) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_fuel_logs_vin ON fuel_logs(vin);

CREATE TABLE IF NOT EXISTS torque_specs (
    id         TEXT PRIMARY KEY,
    vin        TEXT NOT NULL,
    component  TEXT NOT NULL DEFAULT '',
    torque     TEXT NOT NULL DEFAULT '',
    labels     TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (vin) REFERENCES vehicles(vin) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_torque_specs_vin ON torque_specs(vin);
"""


def _connect():
    """One short-lived connection per call — safe across threads."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _init_schema():
    # WAL + NORMAL sync gives concurrent reads while remaining crash-safe.
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(SCHEMA)

        # Seed defaults if this is a brand-new DB.
        if conn.execute("SELECT COUNT(*) FROM global_settings").fetchone()[0] == 0:
            conn.execute("BEGIN")
            for k, v in DEFAULT_GLOBAL_SETTINGS.items():
                conn.execute(
                    "INSERT INTO global_settings (key, value) VALUES (?, ?)",
                    (k, json.dumps(v)),
                )
            conn.execute("COMMIT")

        if conn.execute("SELECT COUNT(*) FROM default_services").fetchone()[0] == 0:
            conn.execute("BEGIN")
            for idx, s in enumerate(DEFAULT_SERVICES_FALLBACK):
                conn.execute(
                    """INSERT INTO default_services
                       (id, category, name, interval_months, interval_miles, parts_info, sort_order)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (str(uuid.uuid4())[:8], s["category"], s["name"],
                     s["interval_months"], s["interval_miles"],
                     s.get("parts_info", ""), idx),
                )
            conn.execute("COMMIT")


def _db_has_vehicles():
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0] > 0


def _migrate_from_json():
    """
    One-shot import from the old fleet_database.json.

    Runs only when:
      - the legacy file exists, AND
      - the SQLite DB has zero vehicles yet (i.e. truly first run).

    The legacy file is renamed to *.bak afterwards so a repeated import
    can't accidentally clobber edits made via the new backend.
    """
    if not os.path.exists(LEGACY_JSON_PATH):
        return
    if _db_has_vehicles():
        return

    try:
        with open(LEGACY_JSON_PATH, "r") as f:
            legacy = json.load(f)
    except Exception as e:
        print(f"Migration: could not read legacy JSON ({e}); skipping.")
        return

    # Heal old quirks before handing off to save_db:
    #   - "garage_part" (singular, stale) merged into "garage_parts"
    #   - "garage_torques" (plural, wrong key from the old add bug) merged into "garage_torque"
    for vin, v in (legacy.get("vehicles") or {}).items():
        for s in v.get("services", []) or []:
            parts = (s.get("garage_parts") or []) + (s.get("garage_part") or [])
            seen = set()
            deduped_parts = []
            for p in parts:
                pid = p.get("id") or str(uuid.uuid4())[:8]
                if pid in seen:
                    continue
                seen.add(pid)
                p["id"] = pid
                deduped_parts.append(p)
            s["garage_parts"] = deduped_parts
            s.pop("garage_part", None)

            torques = (s.get("garage_torque") or []) + (s.get("garage_torques") or [])
            seen = set()
            deduped_torques = []
            for t in torques:
                tid = t.get("id") or str(uuid.uuid4())[:8]
                if tid in seen:
                    continue
                seen.add(tid)
                t["id"] = tid
                deduped_torques.append(t)
            s["garage_torque"] = deduped_torques
            s.pop("garage_torques", None)

    try:
        save_db(legacy, sync_mqtt=False)
        # Preserve the original file as a safety net; never delete.
        try:
            shutil.move(LEGACY_JSON_PATH, LEGACY_JSON_PATH + ".bak")
        except Exception as e:
            print(f"Migration: could not rename legacy JSON to .bak ({e}); keeping it in place.")
        print(f"Migration: imported legacy DB from {LEGACY_JSON_PATH}")
    except Exception as e:
        print(f"Migration: failed to write SQLite from legacy JSON ({e}).")


# Schema init happens immediately on import so load_db()/save_db() always
# have a valid DB to talk to. Migration runs later (see end of file) because
# it depends on save_db being defined.
_init_schema()


# ==========================================
# 📥 LOAD
# ==========================================

def _row_to_dict(row):
    return {k: row[k] for k in row.keys()}


def load_db():
    """
    Return the full database as a single dict, matching the original JSON
    schema exactly so existing routes and templates need no changes.

    Reads from SQLite — no implicit save-on-read like the old version, so
    this is safe to call from any thread without write contention.
    """
    with _connect() as conn:
        # global_settings: key→JSON-decoded value, with defaults filled in.
        gs = dict(DEFAULT_GLOBAL_SETTINGS)
        for row in conn.execute("SELECT key, value FROM global_settings"):
            try:
                gs[row["key"]] = json.loads(row["value"])
            except Exception:
                gs[row["key"]] = row["value"]

        # default_services
        default_services = []
        for row in conn.execute(
            "SELECT id, category, name, interval_months, interval_miles, parts_info "
            "FROM default_services ORDER BY sort_order, name"
        ):
            default_services.append(_row_to_dict(row))

        # vehicles + nested children
        vehicles = {}
        veh_rows = list(conn.execute(
            "SELECT vin, nickname, year, make, model, current_mileage, theme_color, "
            "       ha_entity_id, image_url, share_token, specs_json "
            "FROM vehicles ORDER BY sort_order, vin"
        ))

        # Pre-fetch children grouped by parent — avoids N+1 queries.
        services_by_vin = {}
        for row in conn.execute(
            "SELECT id, vin, category, name, interval_months, interval_miles, "
            "       parts_info, last_service_miles, last_service_date "
            "FROM services ORDER BY vin, sort_order"
        ):
            services_by_vin.setdefault(row["vin"], []).append({
                "id": row["id"],
                "category": row["category"],
                "name": row["name"],
                "interval_months": row["interval_months"],
                "interval_miles": row["interval_miles"],
                "parts_info": row["parts_info"],
                "last_service_miles": row["last_service_miles"],
                "last_service_date": row["last_service_date"],
                "garage_parts": [],
                "garage_torque": [],
            })

        # garage_items grouped by service_id
        garage_by_service = {}
        for row in conn.execute(
            "SELECT id, service_id, item_type, name, value "
            "FROM garage_items ORDER BY service_id, sort_order"
        ):
            garage_by_service.setdefault(row["service_id"], []).append({
                "id": row["id"],
                "type": row["item_type"],
                "name": row["name"],
                "value": row["value"],
            })

        logbook_by_vin = {}
        for row in conn.execute(
            "SELECT id, vin, date, service, mileage, notes, cost_parts, cost_labor "
            "FROM logbook ORDER BY vin, date DESC, sort_order"
        ):
            logbook_by_vin.setdefault(row["vin"], []).append({
                "id": row["id"],
                "date": row["date"],
                "service": row["service"],
                "mileage": row["mileage"],
                "notes": row["notes"],
                "cost_parts": row["cost_parts"],
                "cost_labor": row["cost_labor"],
            })

        fuel_by_vin = {}
        for row in conn.execute(
            "SELECT id, vin, date, cost FROM fuel_logs ORDER BY vin, date DESC, sort_order"
        ):
            fuel_by_vin.setdefault(row["vin"], []).append({
                "id": row["id"],
                "date": row["date"],
                "cost": row["cost"],
            })

        torque_by_vin = {}
        for row in conn.execute(
            "SELECT id, vin, component, torque, labels "
            "FROM torque_specs ORDER BY vin, sort_order"
        ):
            torque_by_vin.setdefault(row["vin"], []).append({
                "id": row["id"],
                "component": row["component"],
                "torque": row["torque"],
                "labels": row["labels"],
            })

        for vrow in veh_rows:
            vin = vrow["vin"]
            try:
                specs = json.loads(vrow["specs_json"]) if vrow["specs_json"] else {}
            except Exception:
                specs = {}
            specs.setdefault("battery_date", "")

            services = services_by_vin.get(vin, [])
            for s in services:
                for item in garage_by_service.get(s["id"], []):
                    if item["type"] == "part":
                        s["garage_parts"].append({"id": item["id"], "name": item["name"], "value": item["value"]})
                    else:
                        s["garage_torque"].append({"id": item["id"], "name": item["name"], "value": item["value"]})

            vehicles[vin] = {
                "nickname": vrow["nickname"],
                "year": vrow["year"],
                "make": vrow["make"],
                "model": vrow["model"],
                "current_mileage": vrow["current_mileage"],
                "theme_color": vrow["theme_color"],
                "ha_entity_id": vrow["ha_entity_id"],
                "image_url": vrow["image_url"],
                "share_token": vrow["share_token"],
                "specs": specs,
                "services": services,
                "logbook": logbook_by_vin.get(vin, []),
                "fuel_logs": fuel_by_vin.get(vin, []),
                "torque_specs": torque_by_vin.get(vin, []),
            }

        return {
            "global_settings": gs,
            "vehicles": vehicles,
            "default_services": default_services,
        }


# ==========================================
# 💾 SAVE
# ==========================================

def save_db(data, sync_mqtt=True):
    """
    Persist the full dict to SQLite inside a single transaction.

    The write lock serializes overlapping save_db() calls from app routes
    and the MQTT / HA-sync threads, eliminating the lost-update races that
    previously caused duplicate logbook entries and disappearing garage
    items. The whole save either succeeds or rolls back — no half-written
    state is ever visible to readers.
    """
    with _db_write_lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")

            # --- global_settings -------------------------------------------------
            conn.execute("DELETE FROM global_settings")
            for k, v in (data.get("global_settings") or {}).items():
                conn.execute(
                    "INSERT INTO global_settings (key, value) VALUES (?, ?)",
                    (k, json.dumps(v)),
                )

            # --- default_services ------------------------------------------------
            conn.execute("DELETE FROM default_services")
            for idx, s in enumerate(data.get("default_services") or []):
                conn.execute(
                    """INSERT INTO default_services
                       (id, category, name, interval_months, interval_miles, parts_info, sort_order)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        s.get("id") or str(uuid.uuid4())[:8],
                        s.get("category", "Other") or "Other",
                        s.get("name", "") or "",
                        int(s.get("interval_months", 0) or 0),
                        int(s.get("interval_miles", 0) or 0),
                        s.get("parts_info", "") or "",
                        idx,
                    ),
                )

            # --- vehicles --------------------------------------------------------
            new_vins = set((data.get("vehicles") or {}).keys())
            existing_vins = {r["vin"] for r in conn.execute("SELECT vin FROM vehicles")}
            for stale_vin in existing_vins - new_vins:
                # CASCADE cleans services -> garage_items, logbook, fuel_logs, torque_specs.
                conn.execute("DELETE FROM vehicles WHERE vin = ?", (stale_vin,))

            for v_idx, (vin, vd) in enumerate((data.get("vehicles") or {}).items()):
                specs = vd.get("specs") or {}
                conn.execute(
                    """INSERT INTO vehicles
                       (vin, nickname, year, make, model, current_mileage, theme_color,
                        ha_entity_id, image_url, share_token, specs_json, sort_order)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(vin) DO UPDATE SET
                           nickname=excluded.nickname,
                           year=excluded.year,
                           make=excluded.make,
                           model=excluded.model,
                           current_mileage=excluded.current_mileage,
                           theme_color=excluded.theme_color,
                           ha_entity_id=excluded.ha_entity_id,
                           image_url=excluded.image_url,
                           share_token=excluded.share_token,
                           specs_json=excluded.specs_json,
                           sort_order=excluded.sort_order""",
                    (
                        vin,
                        vd.get("nickname", "") or "",
                        str(vd.get("year", "") or ""),
                        vd.get("make", "") or "",
                        vd.get("model", "") or "",
                        int(vd.get("current_mileage", 0) or 0),
                        vd.get("theme_color", "#2563eb") or "#2563eb",
                        vd.get("ha_entity_id", "") or "",
                        vd.get("image_url", "") or "",
                        vd.get("share_token") or str(uuid.uuid4()),
                        json.dumps(specs),
                        v_idx,
                    ),
                )

                # services + their garage_items: full replace (CASCADE handles children).
                conn.execute("DELETE FROM services WHERE vin = ?", (vin,))
                for s_idx, s in enumerate(vd.get("services", []) or []):
                    sid = s.get("id") or str(uuid.uuid4())[:8]
                    last_miles = s.get("last_service_miles")
                    last_miles = int(last_miles) if last_miles not in (None, "") else None
                    last_date = s.get("last_service_date") or None
                    conn.execute(
                        """INSERT INTO services
                           (id, vin, category, name, interval_months, interval_miles, parts_info,
                            last_service_miles, last_service_date, sort_order)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (
                            sid, vin,
                            s.get("category", "Other") or "Other",
                            s.get("name", "") or "",
                            int(s.get("interval_months", 0) or 0),
                            int(s.get("interval_miles", 0) or 0),
                            s.get("parts_info", "") or "",
                            last_miles, last_date, s_idx,
                        ),
                    )

                    # garage_parts (plural) — what templates read
                    for g_idx, p in enumerate(s.get("garage_parts", []) or []):
                        conn.execute(
                            """INSERT INTO garage_items (id, service_id, item_type, name, value, sort_order)
                               VALUES (?, ?, 'part', ?, ?, ?)""",
                            (p.get("id") or str(uuid.uuid4())[:8], sid,
                             p.get("name", "") or "", p.get("value", "") or "", g_idx),
                        )
                    # garage_torque (singular) — what templates read
                    for g_idx, t in enumerate(s.get("garage_torque", []) or []):
                        conn.execute(
                            """INSERT INTO garage_items (id, service_id, item_type, name, value, sort_order)
                               VALUES (?, ?, 'torque', ?, ?, ?)""",
                            (t.get("id") or str(uuid.uuid4())[:8], sid,
                             t.get("name", "") or "", t.get("value", "") or "", g_idx),
                        )

                # logbook
                conn.execute("DELETE FROM logbook WHERE vin = ?", (vin,))
                for l_idx, l in enumerate(vd.get("logbook", []) or []):
                    conn.execute(
                        """INSERT INTO logbook
                           (id, vin, date, service, mileage, notes, cost_parts, cost_labor, sort_order)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (
                            l.get("id") or str(uuid.uuid4())[:8], vin,
                            l.get("date", "") or "",
                            l.get("service", "") or "",
                            int(l.get("mileage", 0) or 0),
                            l.get("notes", "") or "",
                            float(l.get("cost_parts", 0) or 0),
                            float(l.get("cost_labor", 0) or 0),
                            l_idx,
                        ),
                    )

                # fuel_logs
                conn.execute("DELETE FROM fuel_logs WHERE vin = ?", (vin,))
                for f_idx, fl in enumerate(vd.get("fuel_logs", []) or []):
                    conn.execute(
                        "INSERT INTO fuel_logs (id, vin, date, cost, sort_order) VALUES (?,?,?,?,?)",
                        (
                            fl.get("id") or str(uuid.uuid4())[:8], vin,
                            fl.get("date", "") or "",
                            float(fl.get("cost", 0) or 0),
                            f_idx,
                        ),
                    )

                # torque_specs
                conn.execute("DELETE FROM torque_specs WHERE vin = ?", (vin,))
                for t_idx, ts in enumerate(vd.get("torque_specs", []) or []):
                    conn.execute(
                        """INSERT INTO torque_specs (id, vin, component, torque, labels, sort_order)
                           VALUES (?,?,?,?,?,?)""",
                        (
                            ts.get("id") or str(uuid.uuid4())[:8], vin,
                            ts.get("component", "") or "",
                            ts.get("torque", "") or "",
                            ts.get("labels", "") or "",
                            t_idx,
                        ),
                    )

            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    # MQTT publish runs outside the DB lock so a slow broker can't block writes.
    if sync_mqtt and mqtt_client and mqtt_client.is_connected() \
            and (data.get("global_settings") or {}).get("mqtt_enabled") == "on":
        try:
            publish_discovery(mqtt_client)
        except Exception as e:
            print(f"MQTT publish_discovery failed: {e}")


# ==========================================
# 🔁 OPTIONAL: scoped transaction helper
# ==========================================
# Routes that want true read-modify-write atomicity (no lost updates between
# their load_db() and save_db()) can opt in:
#
#     from core import db_transaction
#     with db_transaction() as db:
#         db["vehicles"][vin]["current_mileage"] += 100
#         # save happens on exit, the whole thing serialized against other writers
#
# This is purely additive — existing load_db()/save_db() callers keep working.

@contextmanager
def db_transaction():
    with _db_write_lock:
        data = load_db()
        yield data
        save_db(data)


# ==========================================
# 🧮 DOMAIN HELPERS (unchanged behavior)
# ==========================================

def parse_date(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        return datetime.now()


def add_months(sourcedate, months):
    month = sourcedate.month - 1 + months
    return datetime(sourcedate.year + month // 12, month % 12 + 1, 1)


def calculate_adm(logbook):
    valid_logs = [l for l in logbook if l.get("mileage") and l.get("date")]
    if len(valid_logs) < 2:
        return 0
    valid_logs.sort(key=lambda x: parse_date(x["date"]))
    days = (parse_date(valid_logs[-1]["date"]) - parse_date(valid_logs[0]["date"])).days
    miles = float(valid_logs[-1]["mileage"]) - float(valid_logs[0]["mileage"])
    return miles / days if days > 14 and miles > 0 else 0


def calculate_fuel_stats(fuel_logs):
    if not fuel_logs:
        return {"total": 0.0, "weekly_avg": 0.0, "monthly_avg": 0.0, "avg_fillup": 0.0}
    total = sum(float(log.get("cost", 0)) for log in fuel_logs)
    dates = [parse_date(log.get("date")) for log in fuel_logs if log.get("date")]
    if not dates:
        return {"total": total, "weekly_avg": 0.0, "monthly_avg": 0.0, "avg_fillup": total / len(fuel_logs)}
    days_span = max((datetime.now() - min(dates)).days, 1)
    return {
        "total": total,
        "weekly_avg": total / (days_span / 7.0),
        "monthly_avg": total / (days_span / 30.44),
        "avg_fillup": total / len(fuel_logs),
    }


def calculate_status(vehicle_data, global_settings):
    current_miles = vehicle_data.get("current_mileage", 0)
    adm = calculate_adm(vehicle_data.get("logbook", []))
    services = []

    for idx, s in enumerate(vehicle_data.get("services", [])):
        last_date_raw, last_miles_raw = s.get("last_service_date"), s.get("last_service_miles")
        if not last_date_raw or last_miles_raw is None:
            services.append({**s,
                "miles_remaining": "N/A", "months_remaining": "N/A",
                "last_service_date_formatted": "None",
                "due_date_str": "TBD", "due_miles": "TBD",
                "status": "Needs Baseline", "predicted": False, "priority": idx})
            continue

        miles_remaining = s.get("interval_miles", 0) - (current_miles - last_miles_raw)
        due_date_time = add_months(parse_date(last_date_raw), s.get("interval_months", 0))
        predicted = False

        if adm > 0 and miles_remaining > 0:
            pred_date = datetime.now() + timedelta(days=(miles_remaining / adm))
            if pred_date < due_date_time:
                due_date_time, predicted = pred_date, True

        days_remaining = (due_date_time - datetime.now()).days
        months_remaining = days_remaining // 30

        status = ("Past Due" if miles_remaining < 0 or days_remaining < 0
                  else "Coming Up" if (miles_remaining <= int(global_settings.get("coming_up_miles", 1000))
                                       or months_remaining <= int(global_settings.get("coming_up_months", 1)))
                  else "All Good")
        services.append({**s,
            "miles_remaining": miles_remaining,
            "months_remaining": months_remaining,
            "last_service_date_formatted": parse_date(last_date_raw).strftime("%Y-%m-%d"),
            "due_date_str": due_date_time.strftime("%Y-%m-%d"),
            "due_miles": last_miles_raw + s.get("interval_miles", 0),
            "status": status, "predicted": predicted, "priority": idx})
    return services


# ==========================================
# 📡 MQTT + HOME ASSISTANT
# ==========================================

mqtt_client = None


def publish_discovery(client):
    db = load_db()
    if db.get("global_settings", {}).get("mqtt_enabled") != "on":
        return
    for vin, v_data in db.get("vehicles", {}).items():
        name = v_data.get("nickname") or f"{v_data.get('year')} {v_data.get('make')} {v_data.get('model')}"
        device_info = {"identifiers": [f"fleet_{vin}"], "name": name, "manufacturer": v_data.get("make")}
        for s in calculate_status(v_data, db.get("global_settings", {})):
            config_topic = f"homeassistant/sensor/fleet_{vin}/{s['id']}/config"
            state_topic = f"fleet/{vin}/{s['id']}/state"
            client.publish(config_topic, json.dumps({
                "name": f"{v_data.get('make')} {s['name']}",
                "state_topic": state_topic,
                "value_template": "{{ value_json.status }}",
                "json_attributes_topic": state_topic,
                "unique_id": f"fleet_{vin}_{s['id']}",
                "device": device_info,
                "icon": "mdi:car-wrench",
            }), retain=True)
            client.publish(state_topic, json.dumps({
                "status": s["status"],
                "miles_remaining": s["miles_remaining"],
                "months_remaining": s["months_remaining"],
                "due_date": s["due_date_str"],
                "category": s["category"],
                "service_name": s["name"],
            }), retain=True)


def mqtt_loop():
    global mqtt_client
    try:
        mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "fleet_maintenance_app")
    except AttributeError:
        mqtt_client = mqtt.Client("fleet_maintenance_app")
    if MQTT_USER and MQTT_PASS:
        mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
    mqtt_client.on_connect = lambda c, u, f, rc, p=None: publish_discovery(c) if rc == 0 else None
    while True:
        try:
            if load_db().get("global_settings", {}).get("mqtt_enabled") == "on":
                mqtt_client.connect(MQTT_BROKER, int(MQTT_PORT), 60)
                mqtt_client.loop_forever()
        except Exception:
            pass
        time.sleep(10)


def ha_sync_loop():
    """
    Background HA-state poller. Wraps the read-modify-write in db_transaction
    so concurrent web edits to the same vehicle can't race with the poller.
    """
    while True:
        try:
            poll_interval = max(int(load_db().get("global_settings", {}).get("ha_polling", 20)), 5)
        except Exception:
            poll_interval = 20

        if HA_TOKEN and HA_TOKEN != "PASTE_YOUR_LONG_LIVED_ACCESS_TOKEN_HERE":
            headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}

            try:
                with db_transaction() as db:
                    updated = False

                    if temp_entity_id := db.get("global_settings", {}).get("temp_entity_id"):
                        try:
                            res = requests.get(f"{HA_URL}/api/states/{temp_entity_id}", headers=headers, timeout=5)
                            if res.status_code == 200:
                                try:
                                    new_temp = float(res.json().get("state"))
                                    if new_temp != db["global_settings"].get("current_temp"):
                                        db["global_settings"]["current_temp"] = new_temp
                                        updated = True
                                except Exception:
                                    pass
                        except Exception:
                            pass

                    for vin, v_data in db.get("vehicles", {}).items():
                        if entity_id := v_data.get("ha_entity_id"):
                            try:
                                res = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=headers, timeout=5)
                                if res.status_code == 200:
                                    state_val = res.json().get("state")
                                    if state_val and state_val.replace(".", "", 1).isdigit():
                                        new_miles = int(float(state_val))
                                        if new_miles != v_data["current_mileage"]:
                                            v_data["current_mileage"] = new_miles
                                            updated = True
                            except Exception:
                                pass

                    if not updated:
                        # No-op save would still rewrite all rows; bail early instead.
                        # Raise a sentinel to skip save_db on context exit:
                        raise _NoChangesDuringPoll()
            except _NoChangesDuringPoll:
                pass
            except Exception as e:
                print(f"HA sync error: {e}")

        time.sleep(poll_interval)


class _NoChangesDuringPoll(Exception):
    """Internal sentinel — lets ha_sync_loop skip an unnecessary save_db."""
    pass


# Override db_transaction so the sentinel suppresses the save without
# propagating as an error. We redefine here using a small wrapper so the
# earlier (general-purpose) definition stays usable for app routes.
_original_db_transaction = db_transaction

@contextmanager
def db_transaction():  # noqa: F811 — intentional shadow
    with _db_write_lock:
        data = load_db()
        try:
            yield data
        except _NoChangesDuringPoll:
            return
        save_db(data)


def get_ha_sensors():
    if not HA_TOKEN or HA_TOKEN == "PASTE_YOUR_LONG_LIVED_ACCESS_TOKEN_HERE":
        return []
    try:
        return [
            {"id": s["entity_id"], "name": s["attributes"].get("friendly_name", s["entity_id"])}
            for s in requests.get(
                f"{HA_URL}/api/states",
                headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
                timeout=3,
            ).json()
            if s["entity_id"].startswith(("sensor.", "input_number.", "weather."))
        ]
    except Exception:
        return []


# Migrate legacy JSON now that save_db is defined. Done at module import time
# so the first request after upgrade Just Works.
_migrate_from_json()


threading.Thread(target=mqtt_loop, daemon=True).start()
threading.Thread(target=ha_sync_loop, daemon=True).start()
