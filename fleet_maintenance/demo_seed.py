import uuid
from core import load_db, save_db

# Today: 2026-05-12  |  Camry: 54,200 mi  |  F-150: 112,400 mi
#
# Status thresholds (defaults):
#   Needs Baseline : last_service_date or last_service_miles is None
#   Past Due       : miles_remaining < 0  OR  days_remaining < 0
#   Coming Up      : miles_remaining <= 1000  OR  months_remaining <= 1
#   All Good       : everything else
#
# interval_miles=999999 is used for time-only services so mileage never
# triggers a false "Past Due" (the app treats 0 as an active 0-mile interval).

CAMRY_VIN = "DEMO0000000000001"
F150_VIN  = "DEMO0000000000002"


def _id():
    return str(uuid.uuid4())[:8]


def seed_demo_data():
    db = load_db()
    if db.get("vehicles"):
        return  # already seeded

    # ------------------------------------------------------------------
    # 2019 Toyota Camry SE  —  "Daily Driver"  —  54,200 miles
    # Status mix: 3 Past Due, 2 Coming Up, 3 All Good, 2 Needs Baseline
    # ------------------------------------------------------------------
    db.setdefault("vehicles", {})[CAMRY_VIN] = {
        "nickname": "Daily Driver",
        "year": "2019", "make": "Toyota", "model": "Camry SE",
        "current_mileage": 54200,
        "theme_color": "#dc2626",
        "ha_entity_id": "", "image_url": "",
        "share_token": str(uuid.uuid4()),
        "specs": {
            "engine_oil":    "0W-20 Full Synthetic (Toyota-approved)",
            "oil_filter":    "Toyota 04152-YZZA6  /  Denso 150-2076",
            "tire_size":     "235/45R18",
            "tire_pressure": "Front 35 psi  /  Rear 33 psi",
            "wiper_blades":  'Driver 26" (Bosch 26A)  /  Passenger 16" (Bosch H301)',
            "manual_url":    "",
        },

        # ── Services ──────────────────────────────────────────────────
        "services": [

            # ── COMING UP (miles_remaining = 500 ≤ 1000) ────────────
            {
                "id": _id(), "category": "Engine",
                "name": "Oil & Filter Change",
                "interval_months": 6, "interval_miles": 5000,
                "parts_info": "0W-20 Full Synthetic · 4.8 qt",
                "last_service_date": "2025-12-15",
                "last_service_miles": 49700,
                "garage_parts": [
                    {"name": "Oil Filter",          "value": "Toyota 04152-YZZA6"},
                    {"name": "Oil Filter Gasket",   "value": "Included with filter"},
                    {"name": "Engine Oil",          "value": "5 qt 0W-20 Full Synthetic"},
                    {"name": "Drain Plug Washer",   "value": "Toyota 90430-12031 (aluminum)"},
                ],
                "garage_torque": [
                    {"name": "Drain Plug (M14)",    "value": "30 ft-lb"},
                    {"name": "Oil Filter",          "value": "Hand-tight + 3/4 turn"},
                ],
            },

            # ── COMING UP (months_remaining = 1 ≤ 1) ────────────────
            {
                "id": _id(), "category": "Brakes",
                "name": "Brake Inspection",
                "interval_months": 12, "interval_miles": 12000,
                "parts_info": "Check pad thickness, rotor runout, caliper pins",
                "last_service_date": "2025-06-01",
                "last_service_miles": 47500,
                "garage_parts": [
                    {"name": "Brake Cleaner",       "value": "1 can (Brakleen)"},
                    {"name": "Caliper Grease",      "value": "Permatex Ultra-Disc Quiet"},
                ],
                "garage_torque": [
                    {"name": "Front Caliper Bracket Bolts",   "value": "79 ft-lb"},
                    {"name": "Front Caliper Slide Pin Bolts", "value": "25 ft-lb"},
                    {"name": "Rear Caliper Bracket Bolts",    "value": "58 ft-lb"},
                    {"name": "Rear Caliper Slide Pin Bolts",  "value": "16 ft-lb"},
                    {"name": "Lug Nuts (re-torque)",          "value": "76 ft-lb"},
                ],
            },

            # ── PAST DUE (miles_remaining = -5,200) ─────────────────
            {
                "id": _id(), "category": "Tires",
                "name": "Tire Rotation",
                "interval_months": 6, "interval_miles": 5000,
                "parts_info": "",
                "last_service_date": "2025-04-15",
                "last_service_miles": 44000,
                "garage_parts": [],
                "garage_torque": [
                    {"name": "Lug Nuts", "value": "76 ft-lb"},
                ],
            },

            # ── PAST DUE (miles_remaining = -200) ───────────────────
            {
                "id": _id(), "category": "Air",
                "name": "Cabin Air Filter",
                "interval_months": 12, "interval_miles": 15000,
                "parts_info": "Toyota 87139-47010 or equivalent",
                "last_service_date": "2024-06-01",
                "last_service_miles": 39000,
                "garage_parts": [
                    {"name": "Cabin Air Filter", "value": "Toyota 87139-47010"},
                ],
                "garage_torque": [],
            },

            # ── PAST DUE (days_remaining < 0, due 2025-10-01) ───────
            # Uses large interval_miles so mileage never triggers false Past Due
            {
                "id": _id(), "category": "Wipers",
                "name": "Wiper Blades",
                "interval_months": 12, "interval_miles": 999999,
                "parts_info": 'Driver 26" / Passenger 16"',
                "last_service_date": "2024-10-01",
                "last_service_miles": 44200,
                "garage_parts": [
                    {"name": "Driver Blade",    "value": 'Bosch Icon 26A (26")'},
                    {"name": "Passenger Blade", "value": 'Bosch Icon H301 (16")'},
                ],
                "garage_torque": [],
            },

            # ── ALL GOOD (miles_remaining = 34,600 / months_remaining = 21) ─
            {
                "id": _id(), "category": "Electrical",
                "name": "Battery Service",
                "interval_months": 48, "interval_miles": 50000,
                "parts_info": "Test and clean terminals. Replace at ~4 years or if CCA drops below spec.",
                "last_service_date": "2024-02-01",
                "last_service_miles": 38800,
                "garage_parts": [
                    {"name": "Battery",               "value": "Group 35 / Interstate MTX-91/T5"},
                    {"name": "Terminal Protector",    "value": "Permatex Battery Protector"},
                    {"name": "Anti-Corrosion Pads",   "value": "2x felt washers"},
                ],
                "garage_torque": [
                    {"name": "Battery Hold-Down Clamp", "value": "48 in-lb"},
                    {"name": "Negative Terminal Bolt",  "value": "35 in-lb"},
                    {"name": "Positive Terminal Bolt",  "value": "35 in-lb"},
                ],
            },

            # ── ALL GOOD (miles_remaining = 30,800 / months_remaining = 12) ─
            {
                "id": _id(), "category": "Engine",
                "name": "Spark Plugs",
                "interval_months": 60, "interval_miles": 60000,
                "parts_info": "NGK Laser Iridium DILKAR7B11H · 4-cylinder needs 4 plugs",
                "last_service_date": "2022-06-01",
                "last_service_miles": 25000,
                "garage_parts": [
                    {"name": "Spark Plugs (qty 4)", "value": "NGK DILKAR7B11H (Laser Iridium)"},
                    {"name": "Anti-Seize",          "value": "Copper anti-seize (thin coat only)"},
                ],
                "garage_torque": [
                    {"name": "Spark Plugs (M14 x 1.25)", "value": "13 ft-lb"},
                ],
            },

            # ── ALL GOOD (miles_remaining = 36,800 / months_remaining = 36) ─
            {
                "id": _id(), "category": "Cooling",
                "name": "Coolant Flush",
                "interval_months": 60, "interval_miles": 50000,
                "parts_info": "Toyota Super Long Life Coolant (pink) · 50/50 mix · ~9.5 qt system",
                "last_service_date": "2024-05-01",
                "last_service_miles": 41000,
                "garage_parts": [
                    {"name": "Toyota SLLC Coolant", "value": "2x Toyota 00272-1LLAC (1 gal each)"},
                    {"name": "Distilled Water",     "value": "~1 gallon"},
                    {"name": "Drain Pan",           "value": "≥ 3 gal capacity"},
                ],
                "garage_torque": [
                    {"name": "Radiator Drain Plug", "value": "13 ft-lb"},
                ],
            },

            # ── NEEDS BASELINE ───────────────────────────────────────
            {
                "id": _id(), "category": "Brakes",
                "name": "Brake Fluid Flush",
                "interval_months": 24, "interval_miles": 999999,
                "parts_info": "DOT 3 · bleed all four corners · check master cylinder cap seal",
                "last_service_date": None,
                "last_service_miles": None,
                "garage_parts": [
                    {"name": "Brake Fluid",     "value": "Toyota DOT 3 (1 qt)"},
                    {"name": "Vacuum Bleeder",  "value": "Mityvac or similar"},
                ],
                "garage_torque": [
                    {"name": "Bleeder Screws", "value": "62 in-lb (do not overtighten)"},
                ],
            },

            # ── NEEDS BASELINE ───────────────────────────────────────
            {
                "id": _id(), "category": "Drivetrain",
                "name": "Transmission Fluid",
                "interval_months": 60, "interval_miles": 60000,
                "parts_info": "Toyota ATF WS (World Standard) · ~4.3 qt drain-and-fill",
                "last_service_date": None,
                "last_service_miles": None,
                "garage_parts": [
                    {"name": "ATF WS Fluid",       "value": "Toyota 00289-ATFWS (2x 1-qt)"},
                    {"name": "Drain Plug Gasket",  "value": "Toyota 35178-30010"},
                ],
                "garage_torque": [
                    {"name": "Trans Drain Plug",  "value": "17 ft-lb"},
                    {"name": "Trans Fill Plug",   "value": "40 ft-lb"},
                ],
            },
        ],

        # ── Logbook ───────────────────────────────────────────────────
        "logbook": [
            {"id": _id(), "date": "2022-06-01",  "mileage": 25000, "service": "Spark Plugs",         "cost": 82.00,  "notes": "NGK Laser Iridium, DIY"},
            {"id": _id(), "date": "2023-01-20",  "mileage": 29800, "service": "Tire Rotation",       "cost": 0.00,   "notes": "DIY — crossed pattern"},
            {"id": _id(), "date": "2023-03-10",  "mileage": 31200, "service": "Oil & Filter Change", "cost": 41.00,  "notes": "0W-20 full synthetic"},
            {"id": _id(), "date": "2023-07-05",  "mileage": 33500, "service": "Cabin Air Filter",    "cost": 22.00,  "notes": "OEM Toyota filter"},
            {"id": _id(), "date": "2023-09-18",  "mileage": 35800, "service": "Oil & Filter Change", "cost": 43.00,  "notes": ""},
            {"id": _id(), "date": "2023-09-18",  "mileage": 35800, "service": "Tire Rotation",       "cost": 0.00,   "notes": "DIY"},
            {"id": _id(), "date": "2024-02-01",  "mileage": 38800, "service": "Battery Service",     "cost": 189.00, "notes": "Interstate MTX-91/T5 — 3yr free replacement warranty"},
            {"id": _id(), "date": "2024-02-10",  "mileage": 39000, "service": "Oil & Filter Change", "cost": 43.00,  "notes": ""},
            {"id": _id(), "date": "2024-02-10",  "mileage": 39000, "service": "Cabin Air Filter",    "cost": 22.00,  "notes": "Interval reset after battery job"},
            {"id": _id(), "date": "2024-05-01",  "mileage": 41000, "service": "Coolant Flush",       "cost": 96.00,  "notes": "Toyota SLLC 50/50, system fully flushed"},
            {"id": _id(), "date": "2024-08-15",  "mileage": 43200, "service": "Oil & Filter Change", "cost": 45.00,  "notes": ""},
            {"id": _id(), "date": "2024-10-01",  "mileage": 44200, "service": "Wiper Blades",        "cost": 29.00,  "notes": "Bosch Icon driver + passenger"},
            {"id": _id(), "date": "2025-01-10",  "mileage": 46200, "service": "Oil & Filter Change", "cost": 46.00,  "notes": ""},
            {"id": _id(), "date": "2025-04-15",  "mileage": 47500, "service": "Tire Rotation",       "cost": 0.00,   "notes": "DIY — brake check looked good"},
            {"id": _id(), "date": "2025-06-01",  "mileage": 47500, "service": "Brake Inspection",    "cost": 0.00,   "notes": "Front pads ~6mm, rears ~5mm, rotors smooth"},
            {"id": _id(), "date": "2025-08-20",  "mileage": 48900, "service": "Oil & Filter Change", "cost": 47.00,  "notes": ""},
            {"id": _id(), "date": "2025-12-15",  "mileage": 49700, "service": "Oil & Filter Change", "cost": 48.00,  "notes": "Due soon — coming up at 54,700 mi"},
        ],

        # ── Fuel Logs ─────────────────────────────────────────────────
        "fuel_logs": [
            {"id": _id(), "date": "2026-04-28", "mileage": 54200, "gallons": 10.8, "price_per_gallon": 3.49, "total_cost": 37.69},
            {"id": _id(), "date": "2026-04-07", "mileage": 53820, "gallons": 10.5, "price_per_gallon": 3.45, "total_cost": 36.23},
            {"id": _id(), "date": "2026-03-20", "mileage": 53440, "gallons": 11.0, "price_per_gallon": 3.52, "total_cost": 38.72},
            {"id": _id(), "date": "2026-03-01", "mileage": 53060, "gallons": 10.7, "price_per_gallon": 3.48, "total_cost": 37.24},
            {"id": _id(), "date": "2026-02-10", "mileage": 52700, "gallons": 10.9, "price_per_gallon": 3.55, "total_cost": 38.70},
            {"id": _id(), "date": "2026-01-22", "mileage": 52330, "gallons": 11.2, "price_per_gallon": 3.61, "total_cost": 40.43},
            {"id": _id(), "date": "2026-01-03", "mileage": 51940, "gallons": 10.8, "price_per_gallon": 3.58, "total_cost": 38.66},
            {"id": _id(), "date": "2025-12-12", "mileage": 51560, "gallons": 10.6, "price_per_gallon": 3.63, "total_cost": 38.48},
        ],

        # ── Vehicle-level Torque Specs ────────────────────────────────
        "torque_specs": [
            {"id": _id(), "component": "Lug Nuts",                     "torque": "76 ft-lb",          "labels": "wheels, tires"},
            {"id": _id(), "component": "Oil Drain Plug (M14×1.5)",     "torque": "30 ft-lb",          "labels": "engine, oil"},
            {"id": _id(), "component": "Spark Plugs (M14×1.25)",       "torque": "13 ft-lb",          "labels": "engine, ignition"},
            {"id": _id(), "component": "Front Caliper Bracket Bolts",  "torque": "79 ft-lb",          "labels": "brakes, front"},
            {"id": _id(), "component": "Front Caliper Slide Pin Bolts","torque": "25 ft-lb",          "labels": "brakes, front"},
            {"id": _id(), "component": "Rear Caliper Bracket Bolts",   "torque": "58 ft-lb",          "labels": "brakes, rear"},
            {"id": _id(), "component": "Rear Caliper Slide Pin Bolts", "torque": "16 ft-lb",          "labels": "brakes, rear"},
            {"id": _id(), "component": "Battery Hold-Down Clamp",      "torque": "48 in-lb",          "labels": "electrical"},
            {"id": _id(), "component": "Battery Terminal Bolts",       "torque": "35 in-lb",          "labels": "electrical"},
            {"id": _id(), "component": "Front Hub/Axle Nut",           "torque": "217 ft-lb",         "labels": "suspension, wheels, front"},
            {"id": _id(), "component": "Radiator Drain Plug",          "torque": "13 ft-lb",          "labels": "cooling"},
            {"id": _id(), "component": "Trans Drain Plug",             "torque": "17 ft-lb",          "labels": "drivetrain, transmission"},
            {"id": _id(), "component": "Bleeder Screws",               "torque": "62 in-lb",          "labels": "brakes"},
        ],
    }

    # ------------------------------------------------------------------
    # 2015 Ford F-150 XLT 5.0L V8 4WD  —  "Work Truck"  —  112,400 mi
    # Status mix: 2 Past Due, 2 Coming Up, 3 All Good, 3 Needs Baseline
    # ------------------------------------------------------------------
    db["vehicles"][F150_VIN] = {
        "nickname": "Work Truck",
        "year": "2015", "make": "Ford", "model": "F-150 XLT 5.0L V8",
        "current_mileage": 112400,
        "theme_color": "#1d4ed8",
        "ha_entity_id": "", "image_url": "",
        "share_token": str(uuid.uuid4()),
        "specs": {
            "engine_oil":    "5W-30 Full Synthetic (Ford 5.0L V8)",
            "oil_filter":    "Motorcraft FL-820S",
            "tire_size":     "275/65R18",
            "tire_pressure": "Front 35 psi  /  Rear 65 psi (loaded)",
            "wiper_blades":  '22" both sides (Bosch 22A)',
            "manual_url":    "",
        },

        # ── Services ──────────────────────────────────────────────────
        "services": [

            # ── PAST DUE (miles_remaining = -4,900) ─────────────────
            {
                "id": _id(), "category": "Engine",
                "name": "Oil & Filter Change",
                "interval_months": 5, "interval_miles": 7500,
                "parts_info": "5W-30 Full Synthetic · 6 qt",
                "last_service_date": "2025-06-15",
                "last_service_miles": 100000,
                "garage_parts": [
                    {"name": "Oil Filter",          "value": "Motorcraft FL-820S"},
                    {"name": "Engine Oil",          "value": "6 qt 5W-30 Full Synthetic"},
                    {"name": "Drain Plug Washer",   "value": "Ford W701951-S (crush washer)"},
                ],
                "garage_torque": [
                    {"name": "Drain Plug (M14×1.5)", "value": "20 ft-lb"},
                    {"name": "Oil Filter",           "value": "18 ft-lb (or hand-tight + 3/4 turn)"},
                ],
            },

            # ── PAST DUE (miles_remaining = -10,400) ────────────────
            {
                "id": _id(), "category": "Drivetrain",
                "name": "Transmission Fluid",
                "interval_months": 36, "interval_miles": 30000,
                "parts_info": "Motorcraft MERCON LV · 6R80 6-speed · ~13 qt full, ~5 qt drain-and-fill",
                "last_service_date": "2022-01-01",
                "last_service_miles": 72000,
                "garage_parts": [
                    {"name": "Transmission Fluid", "value": "Motorcraft MERCON LV (5x 1-qt)"},
                    {"name": "Trans Filter",       "value": "Motorcraft FT-190"},
                    {"name": "Trans Pan Gasket",   "value": "Ford OEM or Fel-Pro TOS18820"},
                ],
                "garage_torque": [
                    {"name": "Trans Pan Bolts",    "value": "10 ft-lb"},
                    {"name": "Trans Drain Plug",   "value": "26 ft-lb"},
                ],
            },

            # ── COMING UP (months_remaining = 1) ────────────────────
            {
                "id": _id(), "category": "Tires",
                "name": "Tire Rotation",
                "interval_months": 6, "interval_miles": 7500,
                "parts_info": "Rotate per Ford pattern (rearward-cross for non-directional)",
                "last_service_date": "2025-12-15",
                "last_service_miles": 108800,
                "garage_parts": [],
                "garage_torque": [
                    {"name": "Lug Nuts", "value": "150 ft-lb"},
                ],
            },

            # ── COMING UP (miles_remaining = 600 ≤ 1000) ────────────
            {
                "id": _id(), "category": "Air",
                "name": "Engine Air Filter",
                "interval_months": 24, "interval_miles": 30000,
                "parts_info": "Motorcraft FA-1883 or K&N 33-2375 (washable)",
                "last_service_date": "2024-06-01",
                "last_service_miles": 83000,
                "garage_parts": [
                    {"name": "Air Filter", "value": "Motorcraft FA-1883"},
                ],
                "garage_torque": [],
            },

            # ── ALL GOOD (miles_remaining = 77,600 / months_remaining = 27) ─
            {
                "id": _id(), "category": "Engine",
                "name": "Spark Plugs",
                "interval_months": 60, "interval_miles": 100000,
                "parts_info": "Motorcraft SP-537 (iridium) · 5.0L V8 needs 8 plugs · use torque-to-yield method",
                "last_service_date": "2023-08-01",
                "last_service_miles": 90000,
                "garage_parts": [
                    {"name": "Spark Plugs (qty 8)", "value": "Motorcraft SP-537"},
                    {"name": "Dielectric Grease",   "value": "Light coat on boot only"},
                    {"name": "Spark Plug Boot Tool", "value": "Lisle 57750 or equiv."},
                ],
                "garage_torque": [
                    {"name": "Spark Plugs (M14×1.25)", "value": "23 ft-lb"},
                ],
            },

            # ── ALL GOOD (miles_remaining = 32,600 / months_remaining = 19) ─
            {
                "id": _id(), "category": "Cooling",
                "name": "Coolant Flush",
                "interval_months": 60, "interval_miles": 60000,
                "parts_info": "Motorcraft Gold Coolant (VC-7-B) · 50/50 mix · ~20 qt system",
                "last_service_date": "2023-01-01",
                "last_service_miles": 85000,
                "garage_parts": [
                    {"name": "Coolant", "value": "Motorcraft VC-7-B Gold (2x 1-gal)"},
                    {"name": "Distilled Water", "value": "~1 gallon"},
                ],
                "garage_torque": [
                    {"name": "Lower Radiator Drain Plug", "value": "24 in-lb (plastic — careful)"},
                ],
            },

            # ── ALL GOOD (miles_remaining = 17,600 / months_remaining = 8) ─
            {
                "id": _id(), "category": "Drivetrain",
                "name": "Front Differential Service",
                "interval_months": 24, "interval_miles": 30000,
                "parts_info": "Motorcraft SAE 75W-140 Synthetic Axle Lubricant · ~1.7 pt capacity",
                "last_service_date": "2025-02-01",
                "last_service_miles": 100000,
                "garage_parts": [
                    {"name": "Differential Fluid", "value": "Motorcraft SAE 75W-140 (1 qt)"},
                    {"name": "Fill Plug Gasket",   "value": "Ford W714772-S (reusable)"},
                ],
                "garage_torque": [
                    {"name": "Front Diff Fill Plug",  "value": "30 ft-lb"},
                    {"name": "Front Diff Drain Plug", "value": "30 ft-lb"},
                ],
            },

            # ── NEEDS BASELINE ───────────────────────────────────────
            {
                "id": _id(), "category": "Drivetrain",
                "name": "Rear Differential Service",
                "interval_months": 24, "interval_miles": 30000,
                "parts_info": "Motorcraft SAE 75W-140 Synthetic Axle Lubricant · ~3.5 pt capacity",
                "last_service_date": None,
                "last_service_miles": None,
                "garage_parts": [
                    {"name": "Differential Fluid", "value": "Motorcraft SAE 75W-140 (2 qt)"},
                    {"name": "Fill Plug Gasket",   "value": "Ford W714772-S"},
                ],
                "garage_torque": [
                    {"name": "Rear Diff Fill Plug",  "value": "35 ft-lb"},
                    {"name": "Rear Diff Drain Plug", "value": "35 ft-lb"},
                ],
            },

            # ── NEEDS BASELINE ───────────────────────────────────────
            {
                "id": _id(), "category": "Drivetrain",
                "name": "Transfer Case Fluid",
                "interval_months": 60, "interval_miles": 60000,
                "parts_info": "Motorcraft MERCON LV ATF · ~2.3 pt capacity",
                "last_service_date": None,
                "last_service_miles": None,
                "garage_parts": [
                    {"name": "Transfer Case Fluid", "value": "Motorcraft MERCON LV (1 qt)"},
                ],
                "garage_torque": [
                    {"name": "T-Case Fill Plug",  "value": "20 ft-lb"},
                    {"name": "T-Case Drain Plug", "value": "20 ft-lb"},
                ],
            },

            # ── NEEDS BASELINE ───────────────────────────────────────
            {
                "id": _id(), "category": "Brakes",
                "name": "Brake Fluid Flush",
                "interval_months": 24, "interval_miles": 999999,
                "parts_info": "DOT 3 · bleed all four corners starting at rear passenger",
                "last_service_date": None,
                "last_service_miles": None,
                "garage_parts": [
                    {"name": "Brake Fluid",    "value": "Motorcraft DOT 3 (12 oz)"},
                    {"name": "Vacuum Bleeder", "value": "Mityvac MV8000 or similar"},
                ],
                "garage_torque": [
                    {"name": "Bleeder Screws", "value": "80 in-lb"},
                ],
            },
        ],

        # ── Logbook ───────────────────────────────────────────────────
        "logbook": [
            {"id": _id(), "date": "2022-01-01",  "mileage": 72000,  "service": "Transmission Fluid",       "cost": 185.00, "notes": "Motorcraft MERCON LV, filter + pan gasket replaced, dealer"},
            {"id": _id(), "date": "2022-04-10",  "mileage": 75000,  "service": "Oil & Filter Change",      "cost": 68.00,  "notes": ""},
            {"id": _id(), "date": "2022-08-22",  "mileage": 79200,  "service": "Oil & Filter Change",      "cost": 68.00,  "notes": ""},
            {"id": _id(), "date": "2022-08-22",  "mileage": 79200,  "service": "Tire Rotation",            "cost": 0.00,   "notes": "DIY"},
            {"id": _id(), "date": "2022-12-15",  "mileage": 83400,  "service": "Oil & Filter Change",      "cost": 70.00,  "notes": ""},
            {"id": _id(), "date": "2023-01-01",  "mileage": 85000,  "service": "Coolant Flush",            "cost": 145.00, "notes": "Motorcraft Gold coolant, shop service"},
            {"id": _id(), "date": "2023-04-08",  "mileage": 87500,  "service": "Oil & Filter Change",      "cost": 70.00,  "notes": ""},
            {"id": _id(), "date": "2023-08-01",  "mileage": 90000,  "service": "Spark Plugs",              "cost": 210.00, "notes": "Motorcraft SP-537 iridium — all 8, dealer installed"},
            {"id": _id(), "date": "2023-08-01",  "mileage": 90000,  "service": "Oil & Filter Change",      "cost": 70.00,  "notes": ""},
            {"id": _id(), "date": "2024-01-20",  "mileage": 94800,  "service": "Tire Rotation",            "cost": 0.00,   "notes": "DIY"},
            {"id": _id(), "date": "2024-02-05",  "mileage": 95000,  "service": "Oil & Filter Change",      "cost": 72.00,  "notes": ""},
            {"id": _id(), "date": "2024-06-01",  "mileage": 98000,  "service": "Engine Air Filter",        "cost": 28.00,  "notes": "Motorcraft FA-1883"},
            {"id": _id(), "date": "2024-06-01",  "mileage": 98000,  "service": "Oil & Filter Change",      "cost": 72.00,  "notes": ""},
            {"id": _id(), "date": "2025-02-01",  "mileage": 100000, "service": "Front Differential Service","cost": 65.00, "notes": "75W-140 synthetic, DIY — milestone service at 100k"},
            {"id": _id(), "date": "2025-02-01",  "mileage": 100000, "service": "Tire Rotation",            "cost": 0.00,   "notes": "DIY"},
            {"id": _id(), "date": "2025-06-15",  "mileage": 100000, "service": "Oil & Filter Change",      "cost": 74.00,  "notes": "Past due — missed by mileage, schedule next one sooner"},
            {"id": _id(), "date": "2025-12-15",  "mileage": 108800, "service": "Tire Rotation",            "cost": 0.00,   "notes": "DIY — due again soon"},
        ],

        # ── Fuel Logs ─────────────────────────────────────────────────
        "fuel_logs": [
            {"id": _id(), "date": "2026-04-15", "mileage": 112400, "gallons": 18.4, "price_per_gallon": 3.49, "total_cost": 64.22},
            {"id": _id(), "date": "2026-03-25", "mileage": 111960, "gallons": 19.1, "price_per_gallon": 3.45, "total_cost": 65.90},
            {"id": _id(), "date": "2026-03-04", "mileage": 111490, "gallons": 18.7, "price_per_gallon": 3.52, "total_cost": 65.82},
            {"id": _id(), "date": "2026-02-12", "mileage": 111020, "gallons": 19.4, "price_per_gallon": 3.55, "total_cost": 68.87},
            {"id": _id(), "date": "2026-01-22", "mileage": 110550, "gallons": 18.9, "price_per_gallon": 3.61, "total_cost": 68.23},
            {"id": _id(), "date": "2026-01-01", "mileage": 110080, "gallons": 20.1, "price_per_gallon": 3.58, "total_cost": 71.96},
        ],

        # ── Vehicle-level Torque Specs ────────────────────────────────
        "torque_specs": [
            {"id": _id(), "component": "Lug Nuts",                       "torque": "150 ft-lb",   "labels": "wheels, tires"},
            {"id": _id(), "component": "Oil Drain Plug (M14×1.5)",       "torque": "20 ft-lb",    "labels": "engine, oil"},
            {"id": _id(), "component": "Spark Plugs (M14×1.25)",         "torque": "23 ft-lb",    "labels": "engine, ignition"},
            {"id": _id(), "component": "Front Caliper Bracket Bolts",    "torque": "184 ft-lb",   "labels": "brakes, front"},
            {"id": _id(), "component": "Front Caliper Slide Pin Bolts",  "torque": "22 ft-lb",    "labels": "brakes, front"},
            {"id": _id(), "component": "Rear Caliper Bracket Bolts",     "torque": "85 ft-lb",    "labels": "brakes, rear"},
            {"id": _id(), "component": "Rear Caliper Slide Pin Bolts",   "torque": "24 ft-lb",    "labels": "brakes, rear"},
            {"id": _id(), "component": "Front Diff Drain/Fill Plug",     "torque": "30 ft-lb",    "labels": "drivetrain, differential, front"},
            {"id": _id(), "component": "Rear Diff Drain/Fill Plug",      "torque": "35 ft-lb",    "labels": "drivetrain, differential, rear"},
            {"id": _id(), "component": "Transfer Case Drain/Fill Plug",  "torque": "20 ft-lb",    "labels": "drivetrain, transfer"},
            {"id": _id(), "component": "Trans Drain Plug",               "torque": "26 ft-lb",    "labels": "drivetrain, transmission"},
            {"id": _id(), "component": "Trans Pan Bolts",                "torque": "10 ft-lb",    "labels": "drivetrain, transmission"},
            {"id": _id(), "component": "Battery Terminal Bolts",         "torque": "44 in-lb",    "labels": "electrical"},
            {"id": _id(), "component": "Wheel Hub Nut (4WD)",            "torque": "295 ft-lb",   "labels": "suspension, wheels, 4wd"},
            {"id": _id(), "component": "Brake Bleeder Screws",           "torque": "80 in-lb",    "labels": "brakes"},
        ],
    }

    save_db(db)
