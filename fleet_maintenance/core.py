import json
import os
import uuid
import threading
import time
import requests
import paho.mqtt.client as mqtt
from datetime import datetime, timedelta

# ==========================================
# ⚙️ HYBRID CONFIGURATION (HA Add-on OR Standalone)
# ==========================================

# 1. Check if we are running inside Home Assistant Supervisor
IS_HA_ADDON = os.path.exists("/data/options.json") or "SUPERVISOR_TOKEN" in os.environ

if IS_HA_ADDON:
    # Home Assistant Paths
    DB_DIR = "/config" if os.path.exists("/config") else "/data"
    DB_PATH = os.path.join(DB_DIR, "fleet_database.json")
    OPTIONS_PATH = "/data/options.json"
else:
    # Standalone Docker Paths
    DB_DIR = os.path.join(os.getcwd(), "data")
    os.makedirs(DB_DIR, exist_ok=True)
    DB_PATH = os.path.join(DB_DIR, "fleet_database.json")
    OPTIONS_PATH = None

# 2. Load Defaults (Prioritize Environment Variables for Standalone users)
HA_URL = os.environ.get("HA_URL", "http://192.168.1.100:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
MQTT_BROKER = os.environ.get("MQTT_BROKER", "core-mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
MQTT_USER = os.environ.get("MQTT_USER", "addons")
MQTT_PASS = os.environ.get("MQTT_PASS", "")

# 3. Override with Home Assistant options if they exist
if IS_HA_ADDON and OPTIONS_PATH and os.path.exists(OPTIONS_PATH):
    try:
        with open(OPTIONS_PATH, 'r') as f:
            config = json.load(f)
            HA_URL = config.get("ha_url", HA_URL)
            HA_TOKEN = config.get("ha_token", HA_TOKEN)
            MQTT_BROKER = config.get("mqtt_broker", MQTT_BROKER)
            MQTT_PORT = int(config.get("mqtt_port", MQTT_PORT))
            MQTT_USER = config.get("mqtt_user", MQTT_USER)
            MQTT_PASS = config.get("mqtt_pass", MQTT_PASS)
    except Exception as e:
        print(f"Error loading HA options: {e}")

DEFAULT_SERVICES_FALLBACK = [
    {"category": "Engine", "name": "Engine Oil & Filter", "interval_months": 12, "interval_miles": 5000, "parts_info": ""},
    {"category": "Engine", "name": "Air Filter", "interval_months": 24, "interval_miles": 30000, "parts_info": ""},
    {"category": "Brakes", "name": "Brake Pads", "interval_months": 60, "interval_miles": 50000, "parts_info": ""},
    {"category": "Steering", "name": "Rotate Tires", "interval_months": 12, "interval_miles": 10000, "parts_info": ""},
]

mqtt_client = None

def load_db():
    if not os.path.exists(DB_PATH): 
        return {
            "global_settings": {"coming_up_miles": 1000, "coming_up_months": 1, "unit": "mi", "currency": "$", "date_format": "YYYY-MM-DD", "ha_polling": 20, "mqtt_enabled": "on", "temp_entity_id": "", "current_temp": None},
            "vehicles": {}, 
            "default_services": [dict(s, id=str(uuid.uuid4())[:8]) for s in DEFAULT_SERVICES_FALLBACK]
        }
    with open(DB_PATH, 'r') as f: db = json.load(f)
    modified = False
    
    gs = db.setdefault("global_settings", {})
    for key, val in [("unit", "mi"), ("currency", "$"), ("date_format", "YYYY-MM-DD"), ("ha_polling", 20), ("mqtt_enabled", "on"), ("coming_up_miles", 1000), ("coming_up_months", 1), ("temp_entity_id", ""), ("current_temp", None)]:
        if key not in gs: gs[key] = val; modified = True
        
    if "default_services" not in db:
        db["default_services"] = [dict(s, id=str(uuid.uuid4())[:8]) for s in DEFAULT_SERVICES_FALLBACK]
        modified = True

    for s in db.get("default_services", []):
        if "parts_info" not in s: s["parts_info"] = ""; modified = True

    for vin, v_data in db.get("vehicles", {}).items():
        for key in ["theme_color", "ha_entity_id", "image_url", "nickname"]:
            if key not in v_data: v_data[key] = "" if key != "theme_color" else "#2563eb"; modified = True
            
        if "share_token" not in v_data: v_data["share_token"] = str(uuid.uuid4()); modified = True
        if "specs" not in v_data: v_data["specs"] = {}; modified = True
        if "battery_date" not in v_data["specs"]: v_data["specs"]["battery_date"] = ""; modified = True
        
        if "fuel_logs" not in v_data: v_data["fuel_logs"] = []; modified = True
        if "torque_specs" not in v_data: v_data["torque_specs"] = []; modified = True
        
        for flog in v_data.get("fuel_logs", []):
            if "id" not in flog: flog["id"] = str(uuid.uuid4())[:8]; modified = True
        for tspec in v_data.get("torque_specs", []):
            if "id" not in tspec: tspec["id"] = str(uuid.uuid4())[:8]; modified = True
            # --- PHASE 3.5: ADDED LABELS ---
            if "labels" not in tspec: tspec["labels"] = ""; modified = True
            
        for s in v_data.get("services", []):
            if "parts_info" not in s: s["parts_info"] = ""; modified = True
            if "garage_parts" not in s: s["garage_parts"] = []; modified = True
            if "garage_torque" not in s: s["garage_torque"] = []; modified = True
            
        for log in v_data.get("logbook", []):
            if "id" not in log: log["id"] = str(uuid.uuid4())[:8]; modified = True
            if "cost_parts" not in log: log["cost_parts"] = 0.0; modified = True
            if "cost_labor" not in log: log["cost_labor"] = 0.0; modified = True
            
    if modified: save_db(db, sync_mqtt=False)
    return db

def save_db(data, sync_mqtt=True):
    with open(DB_PATH, 'w') as f: json.dump(data, f, indent=2)
    if sync_mqtt and mqtt_client and mqtt_client.is_connected() and data.get("global_settings", {}).get("mqtt_enabled") == "on":
        publish_discovery(mqtt_client)

def parse_date(date_str):
    try: return datetime.strptime(date_str, "%Y-%m-%d")
    except: return datetime.now()

def add_months(sourcedate, months):
    month = sourcedate.month - 1 + months
    return datetime(sourcedate.year + month // 12, month % 12 + 1, 1)

def calculate_adm(logbook):
    valid_logs = [l for l in logbook if l.get("mileage") and l.get("date")]
    if len(valid_logs) < 2: return 0
    valid_logs.sort(key=lambda x: parse_date(x["date"]))
    days = (parse_date(valid_logs[-1]["date"]) - parse_date(valid_logs[0]["date"])).days
    miles = float(valid_logs[-1]["mileage"]) - float(valid_logs[0]["mileage"])
    return miles / days if days > 14 and miles > 0 else 0

def calculate_fuel_stats(fuel_logs):
    if not fuel_logs: return {"total": 0.0, "weekly_avg": 0.0, "monthly_avg": 0.0, "avg_fillup": 0.0}
    total = sum(float(log.get("cost", 0)) for log in fuel_logs)
    dates = [parse_date(log.get("date")) for log in fuel_logs if log.get("date")]
    if not dates: return {"total": total, "weekly_avg": 0.0, "monthly_avg": 0.0, "avg_fillup": total / len(fuel_logs)}
    days_span = max((datetime.now() - min(dates)).days, 1)
    return {"total": total, "weekly_avg": total / (days_span / 7.0), "monthly_avg": total / (days_span / 30.44), "avg_fillup": total / len(fuel_logs)}

def calculate_status(vehicle_data, global_settings):
    current_miles = vehicle_data.get("current_mileage", 0)
    adm = calculate_adm(vehicle_data.get("logbook", []))
    services = []
    
    for idx, s in enumerate(vehicle_data.get("services", [])):
        last_date_raw, last_miles_raw = s.get("last_service_date"), s.get("last_service_miles")
        if not last_date_raw or last_miles_raw is None:
            services.append({**s, "miles_remaining": "N/A", "months_remaining": "N/A", "last_service_date_formatted": "None", "due_date_str": "TBD", "due_miles": "TBD", "status": "Needs Baseline", "predicted": False, "priority": idx})
            continue
            
        miles_remaining = s.get("interval_miles", 0) - (current_miles - last_miles_raw)
        due_date_time = add_months(parse_date(last_date_raw), s.get("interval_months", 0))
        predicted = False
        
        if adm > 0 and miles_remaining > 0:
            pred_date = datetime.now() + timedelta(days=(miles_remaining / adm))
            if pred_date < due_date_time: due_date_time, predicted = pred_date, True

        days_remaining = (due_date_time - datetime.now()).days
        months_remaining = days_remaining // 30
        
        status = "Past Due" if miles_remaining < 0 or days_remaining < 0 else "Coming Up" if miles_remaining <= int(global_settings.get("coming_up_miles", 1000)) or months_remaining <= int(global_settings.get("coming_up_months", 1)) else "All Good"
        services.append({**s, "miles_remaining": miles_remaining, "months_remaining": months_remaining, "last_service_date_formatted": parse_date(last_date_raw).strftime("%Y-%m-%d"), "due_date_str": due_date_time.strftime("%Y-%m-%d"), "due_miles": last_miles_raw + s.get("interval_miles", 0), "status": status, "predicted": predicted, "priority": idx})
    return services

def publish_discovery(client):
    db = load_db()
    if db.get("global_settings", {}).get("mqtt_enabled") != "on": return
    for vin, v_data in db.get("vehicles", {}).items():
        name = v_data.get('nickname') or f"{v_data.get('year')} {v_data.get('make')} {v_data.get('model')}"
        device_info = {"identifiers": [f"fleet_{vin}"], "name": name, "manufacturer": v_data.get('make')}
        for s in calculate_status(v_data, db.get("global_settings", {})):
            config_topic = f"homeassistant/sensor/fleet_{vin}/{s['id']}/config"
            state_topic = f"fleet/{vin}/{s['id']}/state"
            client.publish(config_topic, json.dumps({"name": f"{v_data.get('make')} {s['name']}", "state_topic": state_topic, "value_template": "{{ value_json.status }}", "json_attributes_topic": state_topic, "unique_id": f"fleet_{vin}_{s['id']}", "device": device_info, "icon": "mdi:car-wrench"}), retain=True)
            client.publish(state_topic, json.dumps({"status": s['status'], "miles_remaining": s['miles_remaining'], "months_remaining": s['months_remaining'], "due_date": s['due_date_str'], "category": s['category'], "service_name": s['name']}), retain=True)

def mqtt_loop():
    global mqtt_client
    try: mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "fleet_maintenance_app")
    except AttributeError: mqtt_client = mqtt.Client("fleet_maintenance_app")
    if MQTT_USER and MQTT_PASS: mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
    mqtt_client.on_connect = lambda c, u, f, rc, p=None: publish_discovery(c) if rc == 0 else None
    while True:
        try:
            if load_db().get("global_settings", {}).get("mqtt_enabled") == "on":
                mqtt_client.connect(MQTT_BROKER, int(MQTT_PORT), 60)
                mqtt_client.loop_forever()
        except: pass
        time.sleep(10)

def ha_sync_loop():
    while True:
        db = load_db()
        poll_interval = max(int(db.get("global_settings", {}).get("ha_polling", 20)), 5)
        if HA_TOKEN and HA_TOKEN != "PASTE_YOUR_LONG_LIVED_ACCESS_TOKEN_HERE":
            updated = False
            headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
            
            if temp_entity_id := db.get("global_settings", {}).get("temp_entity_id"):
                try:
                    res = requests.get(f"{HA_URL}/api/states/{temp_entity_id}", headers=headers, timeout=5)
                    if res.status_code == 200:
                        try:
                            new_temp = float(res.json().get('state'))
                            if new_temp != db["global_settings"].get("current_temp"):
                                db["global_settings"]["current_temp"] = new_temp
                                updated = True
                        except: pass
                except: pass

            for vin, v_data in db.get("vehicles", {}).items():
                if entity_id := v_data.get("ha_entity_id"):
                    try:
                        res = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=headers, timeout=5)
                        if res.status_code == 200 and (state_val := res.json().get('state')) and state_val.replace('.', '', 1).isdigit():
                            if int(float(state_val)) != v_data["current_mileage"]:
                                v_data["current_mileage"] = int(float(state_val))
                                updated = True
                    except: pass
            if updated: save_db(db)
        time.sleep(poll_interval)

def get_ha_sensors():
    if not HA_TOKEN or HA_TOKEN == "PASTE_YOUR_LONG_LIVED_ACCESS_TOKEN_HERE": return []
    try:
        return [{"id": s['entity_id'], "name": s['attributes'].get('friendly_name', s['entity_id'])} for s in requests.get(f"{HA_URL}/api/states", headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}, timeout=3).json() if s['entity_id'].startswith(('sensor.', 'input_number.', 'weather.'))]
    except: return []

threading.Thread(target=mqtt_loop, daemon=True).start()
threading.Thread(target=ha_sync_loop, daemon=True).start()