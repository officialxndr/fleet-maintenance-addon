import uuid
from core import load_db, save_db


def seed_demo_data():
    db = load_db()
    if db.get('vehicles'):
        return  # already seeded

    camry_vin = "DEMO0000000000001"
    f150_vin  = "DEMO0000000000002"

    db.setdefault('vehicles', {})[camry_vin] = {
        "nickname": "Daily Driver",
        "year": "2019", "make": "Toyota", "model": "Camry",
        "current_mileage": 54200,
        "theme_color": "#dc2626",
        "ha_entity_id": "", "image_url": "",
        "share_token": str(uuid.uuid4()),
        "specs": {
            "engine_oil": "0W-20", "oil_filter": "Toyota 04152-YZZA6",
            "tire_size": "235/45R18", "tire_pressure": "35 psi",
            "wiper_blades": "26\" / 16\"", "manual_url": "",
        },
        "services": [
            {
                "id": str(uuid.uuid4())[:8], "category": "Engine",
                "name": "Oil & Filter Change",
                "interval_months": 6, "interval_miles": 5000,
                "parts_info": "0W-20 Full Synthetic, 4.8 qt",
                "last_date": "2025-10-01", "last_mileage": 51000,
                "garage_parts": [
                    {"name": "Oil Filter", "value": "Toyota 04152-YZZA6"},
                    {"name": "Drain Plug Gasket", "value": "M14"},
                ],
                "garage_torque": [{"name": "Drain Plug", "value": "30 ft-lb"}],
            },
            {
                "id": str(uuid.uuid4())[:8], "category": "Tires",
                "name": "Tire Rotation",
                "interval_months": 6, "interval_miles": 5000,
                "parts_info": "",
                "last_date": "2025-10-01", "last_mileage": 51000,
                "garage_parts": [],
                "garage_torque": [{"name": "Lug Nuts", "value": "76 ft-lb"}],
            },
            {
                "id": str(uuid.uuid4())[:8], "category": "Brakes",
                "name": "Brake Fluid Flush",
                "interval_months": 24, "interval_miles": 0,
                "parts_info": "DOT 3",
                "last_date": "2023-06-15", "last_mileage": 38000,
                "garage_parts": [], "garage_torque": [],
            },
        ],
        "logbook": [
            {
                "id": str(uuid.uuid4())[:8], "date": "2025-10-01", "mileage": 51000,
                "service": "Oil & Filter Change", "cost": 45.00, "notes": "Full synthetic",
            },
            {
                "id": str(uuid.uuid4())[:8], "date": "2025-04-10", "mileage": 46500,
                "service": "Oil & Filter Change", "cost": 43.00, "notes": "",
            },
        ],
        "fuel_logs": [
            {
                "id": str(uuid.uuid4())[:8], "date": "2026-04-28", "mileage": 54200,
                "gallons": 11.2, "price_per_gallon": 3.49, "total_cost": 39.09,
            },
            {
                "id": str(uuid.uuid4())[:8], "date": "2026-04-10", "mileage": 53850,
                "gallons": 10.8, "price_per_gallon": 3.55, "total_cost": 38.34,
            },
        ],
        "torque_specs": [
            {"id": str(uuid.uuid4())[:8], "component": "Lug Nuts", "torque": "76 ft-lb", "labels": "wheels"},
            {"id": str(uuid.uuid4())[:8], "component": "Oil Drain Plug", "torque": "30 ft-lb", "labels": "engine"},
        ],
    }

    db['vehicles'][f150_vin] = {
        "nickname": "Work Truck",
        "year": "2015", "make": "Ford", "model": "F-150",
        "current_mileage": 112400,
        "theme_color": "#1d4ed8",
        "ha_entity_id": "", "image_url": "",
        "share_token": str(uuid.uuid4()),
        "specs": {
            "engine_oil": "5W-30", "oil_filter": "Motorcraft FL-820S",
            "tire_size": "275/65R18", "tire_pressure": "35 psi front / 65 psi rear",
            "wiper_blades": "22\" / 22\"", "manual_url": "",
        },
        "services": [
            {
                "id": str(uuid.uuid4())[:8], "category": "Engine",
                "name": "Oil & Filter Change",
                "interval_months": 6, "interval_miles": 7500,
                "parts_info": "5W-30 Motorcraft, 6 qt",
                "last_date": "2025-11-15", "last_mileage": 108500,
                "garage_parts": [{"name": "Oil Filter", "value": "Motorcraft FL-820S"}],
                "garage_torque": [{"name": "Drain Plug", "value": "21 ft-lb"}],
            },
            {
                "id": str(uuid.uuid4())[:8], "category": "Drivetrain",
                "name": "Transfer Case Fluid",
                "interval_months": 60, "interval_miles": 60000,
                "parts_info": "Motorcraft XL-12",
                "last_date": "2021-03-20", "last_mileage": 72000,
                "garage_parts": [], "garage_torque": [],
            },
            {
                "id": str(uuid.uuid4())[:8], "category": "Air",
                "name": "Engine Air Filter",
                "interval_months": 24, "interval_miles": 30000,
                "parts_info": "",
                "last_date": "2024-05-01", "last_mileage": 95000,
                "garage_parts": [], "garage_torque": [],
            },
        ],
        "logbook": [
            {
                "id": str(uuid.uuid4())[:8], "date": "2025-11-15", "mileage": 108500,
                "service": "Oil & Filter Change", "cost": 62.00, "notes": "Dealer service",
            },
        ],
        "fuel_logs": [],
        "torque_specs": [
            {"id": str(uuid.uuid4())[:8], "component": "Lug Nuts", "torque": "150 ft-lb", "labels": "wheels"},
        ],
    }

    save_db(db)
