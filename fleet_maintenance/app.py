from flask import Flask, request, jsonify, render_template, redirect, Response, send_from_directory
import csv
import json  # FIX: previously missing; broke /api/export_db
import re
import uuid
import requests
from datetime import datetime
from io import StringIO
from core import load_db, save_db, calculate_status, calculate_fuel_stats, calculate_adm, get_ha_sensors, parse_date, mqtt_client
import community_blueprints as cbp

app = Flask(__name__, static_folder='static')

# Canonical list of UI tabs (matches button ids in templates/index.html minus the "btn-" prefix).
ALL_TABS = ["summary", "timeline", "intervals", "logbook", "fuel", "specs"]

# Standard 17-char VIN: alphanumeric, no I/O/Q (banned to avoid confusion with 1/0).
# Vehicles pre-1981 sometimes use shorter codes; users who have one of those can
# simply leave the field blank and we'll generate a LOCAL-xxxxxxxx id instead.
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

def _is_valid_vin(s):
    return bool(_VIN_RE.match((s or "").strip().upper()))

def _generate_local_id():
    """Identifier used in place of a VIN when the user leaves it blank."""
    return "LOCAL-" + str(uuid.uuid4())[:8].upper()

@app.context_processor
def inject_ingress_path(): return dict(ingress_path=request.headers.get("X-Ingress-Path", ""))

def get_base_path(): return request.headers.get("X-Ingress-Path", "")

@app.template_filter('format_date')
def format_date_filter(date_str, fmt_choice):
    if not date_str or date_str in ["TBD", "None", "-"]: return date_str
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        if fmt_choice == "MM/DD/YYYY": return d.strftime("%m/%d/%Y")
        if fmt_choice == "DD/MM/YYYY": return d.strftime("%d/%m/%Y")
        return date_str
    except: return date_str

@app.route('/manifest.json')
def manifest(): return send_from_directory('static', 'manifest.json')

@app.route('/sw.js')
def service_worker(): return send_from_directory('static', 'sw.js')

@app.route('/')
def index():
    db = load_db()
    vehicles = db.get("vehicles", {})
    if not vehicles: return render_template('index.html', current_vin=None, vehicles={}, vehicle_data=None, default_services=db.get("default_services", []), global_settings=db.get("global_settings", {}))
    return redirect(f"{get_base_path()}/vehicle/{list(vehicles.keys())[0]}")

@app.route('/vehicle/<vin>')
def view_vehicle(vin):
    db = load_db()
    global_settings = db.get("global_settings", {})
    if vin not in db.get("vehicles", {}): return redirect(f"{get_base_path()}/")
    v_data = db["vehicles"][vin]
    
    services = calculate_status(v_data, global_settings)
    services.sort(key=lambda x: ({"Past Due": 0, "Needs Baseline": 1, "Coming Up": 2, "All Good": 3}.get(x["status"], 4), x.get("miles_remaining") if isinstance(x.get("miles_remaining"), int) else 999999))
    
    fuel_stats = calculate_fuel_stats(v_data.get("fuel_logs", []))
    total_parts = sum(float(l.get("cost_parts", 0)) for l in v_data.get("logbook", []))
    total_labor = sum(float(l.get("cost_labor", 0)) for l in v_data.get("logbook", []))
    tco = {"parts": total_parts, "labor": total_labor, "fuel": fuel_stats["total"], "total": total_parts + total_labor + fuel_stats["total"]}
    
    battery_risk = False
    b_date_str = v_data.get("specs", {}).get("battery_date")
    curr_temp = global_settings.get("current_temp")
    if b_date_str and curr_temp is not None:
        try:
            b_date = datetime.strptime(b_date_str, "%Y-%m-%d")
            if (datetime.now() - b_date).days > 1460 and curr_temp < 0:
                battery_risk = True
        except: pass
    
    return render_template('index.html', current_vin=vin, vehicles=db["vehicles"], vehicle_data=v_data, services=services, ha_sensors=get_ha_sensors(), today_date=datetime.now().strftime("%Y-%m-%d"), default_services=db.get("default_services", []), tco=tco, adm=calculate_adm(v_data.get("logbook", [])), fuel_stats=fuel_stats, global_settings=global_settings, battery_risk=battery_risk)

@app.route('/share/<token>')
def shared_view(token):
    db = load_db()
    global_settings = db.get("global_settings", {})
    for vin, v_data in db.get("vehicles", {}).items():
        if v_data.get("share_token") == token:
            services = calculate_status(v_data, global_settings)
            services.sort(key=lambda x: ({"Past Due": 0, "Needs Baseline": 1, "Coming Up": 2, "All Good": 3}.get(x["status"], 4), x.get("miles_remaining") if isinstance(x.get("miles_remaining"), int) else 999999))
            total_parts = sum(float(l.get("cost_parts", 0)) for l in v_data.get("logbook", []))
            total_labor = sum(float(l.get("cost_labor", 0)) for l in v_data.get("logbook", []))
            fuel_stats = calculate_fuel_stats(v_data.get("fuel_logs", []))
            tco = {"parts": total_parts, "labor": total_labor, "fuel": fuel_stats["total"], "total": total_parts + total_labor + fuel_stats["total"]}
            return render_template('shared.html', current_vin=vin, vehicle_data=v_data, services=services, today_date=datetime.now().strftime("%Y-%m-%d"), fuel_stats=fuel_stats, tco=tco, global_settings=global_settings, shared_mode=True)
    return "Invalid or Expired Share Link", 404

@app.route('/api/update_global_settings', methods=['POST'])
def update_global_settings():
    db = load_db()
    gs = db.setdefault("global_settings", {})
    for key in ["coming_up_miles", "coming_up_months", "ha_polling"]: gs[key] = int(request.form.get(key, gs[key]))
    for key in ["unit", "currency", "date_format", "mqtt_enabled", "temp_entity_id"]: gs[key] = request.form.get(key, gs.get(key))
    if "mqtt_enabled" not in request.form: gs["mqtt_enabled"] = "off"

    # Tab visibility: the form sends a `visible_tab` checkbox per tab the
    # user wants visible. Anything in ALL_TABS that's NOT in the submitted
    # set is hidden. Stored as a comma-separated string for simplicity.
    if "tab_visibility_submitted" in request.form:
        visible = set(request.form.getlist("visible_tab"))
        gs["hidden_tabs"] = ",".join(t for t in ALL_TABS if t not in visible)

    save_db(db)
    return redirect(request.headers.get("Referer", f"{get_base_path()}/"))

@app.route('/api/vacuum_db', methods=['POST'])
def vacuum_db():
    db = load_db()
    for vin, v_data in db.get("vehicles", {}).items():
        v_data["services"] = [s for s in v_data.get("services", []) if s.get("name") and s.get("name").strip()]
        v_data["logbook"] = [l for l in v_data.get("logbook", []) if l.get("date") and l.get("service")]
        v_data["fuel_logs"] = [f for f in v_data.get("fuel_logs", []) if f.get("date") and f.get("cost", 0) > 0]
    save_db(db)
    return redirect(request.headers.get("Referer", f"{get_base_path()}/"))

@app.route('/api/export_db')
def export_db():
    return Response(json.dumps(load_db(), indent=2), mimetype="application/json", headers={"Content-Disposition": "attachment;filename=fleet_database_backup.json"})

@app.route('/api/<vin>/live_data')
def live_data(vin): return jsonify({"current_mileage": load_db().get("vehicles", {}).get(vin, {}).get("current_mileage", 0)})

@app.route('/api/decode_vin/<vin>')
def decode_vin(vin):
    try:
        res = requests.get(f"https://vpic.nhtsa.dot.gov/api/vehicles/decodevin/{vin}?format=json").json()
        return jsonify({item['Variable'].split()[-1].lower(): item.get('Value') for item in res.get('Results', []) if item.get('Value') and item['Variable'] in ['Make', 'Model', 'Model Year']})
    except: return jsonify({})

@app.route('/api/add_vehicle', methods=['POST'])
def add_vehicle():
    db = load_db()
    raw_vin = (request.form.get('vin', '') or '').strip().upper()

    # VIN policy:
    #   - blank → auto-generate LOCAL-xxxxxxxx so users without a VIN can still add the car
    #   - non-blank → must be a valid 17-char VIN; otherwise show an error and bounce back
    if raw_vin == "":
        vin = _generate_local_id()
        # collision guard, just in case
        while vin in db.get("vehicles", {}):
            vin = _generate_local_id()
    else:
        if not _is_valid_vin(raw_vin):
            # Send the user back to the add-vehicle form with a friendly error banner.
            return redirect(f"{get_base_path()}/?tab=add&add_error=invalid_vin")
        if raw_vin in db.get("vehicles", {}):
            return redirect(f"{get_base_path()}/?tab=add&add_error=duplicate_vin")
        vin = raw_vin

    # Starting mileage defaults to 0 — especially useful when a Home Assistant
    # entity is linked, since the value gets overwritten by the poller shortly.
    try:
        mileage = int((request.form.get('mileage') or '0').strip() or 0)
    except ValueError:
        mileage = 0

    blueprint_id = request.form.get('blueprint_id', '').strip()
    blueprint_source = request.form.get('blueprint_source', '')  # 'local' or 'community'
    selected_services = request.form.getlist('selected_services')  # empty = import all
    import_specs = request.form.get('import_specs') == 'yes'
    import_torque = request.form.get('import_torque') == 'yes'

    new_services_config = []
    initial_specs = {"battery_date": ""}
    initial_torque = []

    if blueprint_id:
        bp_data = None
        if blueprint_source == 'local':
            entry = cbp.get_local_blueprint(blueprint_id)
            bp_data = entry.get('data') if entry else None
        else:
            bp_data = cbp.fetch_community_blueprint(blueprint_id)

        if bp_data:
            for s in bp_data.get('services', []):
                if selected_services and s.get('name', '') not in selected_services:
                    continue
                new_services_config.append({
                    "id": str(uuid.uuid4())[:8],
                    "category": str(s.get("category", "Other")).strip() or "Other",
                    "name": str(s.get("name", "")).strip(),
                    "interval_months": int(s.get("interval_months", 0)),
                    "interval_miles": int(s.get("interval_miles", 0)),
                    "parts_info": str(s.get("parts_info", "")).strip(),
                    "last_service_miles": None,
                    "last_service_date": None,
                    "garage_parts": [{"id": str(uuid.uuid4())[:8], "name": str(p.get("name", "")), "value": str(p.get("value", ""))} for p in (s.get("garage_parts") or [])],
                    "garage_torque": [{"id": str(uuid.uuid4())[:8], "name": str(t.get("name", "")), "value": str(t.get("value", ""))} for t in (s.get("garage_torque") or [])],
                })
            if import_specs and isinstance(bp_data.get('specs'), dict):
                for key in ("engine_oil", "oil_filter", "tire_size", "tire_pressure", "wiper_blades", "manual_url"):
                    if bp_data['specs'].get(key):
                        initial_specs[key] = str(bp_data['specs'][key]).strip()
            if import_torque:
                for t in bp_data.get('torque_specs', []):
                    initial_torque.append({"id": str(uuid.uuid4())[:8], "component": str(t.get("component", "")).strip(), "torque": str(t.get("torque", "")).strip(), "labels": str(t.get("labels", "")).strip()})

    if not new_services_config and not blueprint_id:
        if 'csv_file' in request.files and request.files['csv_file'].filename != '':
            for row in csv.DictReader(StringIO(request.files['csv_file'].stream.read().decode("UTF8"), newline=None)):
                new_services_config.append({"id": str(uuid.uuid4())[:8], "category": row.get('Category', 'Other').strip(), "name": row.get('Service', 'Unknown').strip(), "interval_months": int(row.get('Interval_Months', 0)), "interval_miles": int(row.get('Interval_Miles', 0)), "parts_info": row.get('Parts_Info', '').strip()})
        else:
            new_services_config = [dict(s, id=str(uuid.uuid4())[:8]) for s in db.get("default_services", [])]

    if request.form.get('update_baseline') == 'yes' and new_services_config:
        db["default_services"] = [{"id": str(uuid.uuid4())[:8], "category": s["category"], "name": s["name"], "interval_months": s["interval_months"], "interval_miles": s["interval_miles"], "parts_info": s.get("parts_info", "")} for s in new_services_config]

    if blueprint_id:
        svc_list = new_services_config  # already has garage_parts/torque from blueprint
    else:
        svc_list = [dict(s, last_service_miles=None, last_service_date=None, garage_parts=[], garage_torque=[]) for s in new_services_config]

    db.setdefault("vehicles", {})[vin] = {
        "nickname": request.form.get('nickname', '').strip(),
        "year": request.form.get('year', ''),
        "make": request.form.get('make', ''),
        "model": request.form.get('model', ''),
        "current_mileage": mileage,
        "theme_color": "#2563eb",
        "ha_entity_id": request.form.get('ha_entity_id', '').strip(),
        "image_url": request.form.get('image_url', '').strip(),
        "services": svc_list,
        "logbook": [],
        "specs": initial_specs,
        "fuel_logs": [],
        "torque_specs": initial_torque,
        "share_token": str(uuid.uuid4()),
    }
    save_db(db)
    return redirect(f"{get_base_path()}/vehicle/{vin}")

@app.route('/api/<vin>/update_vehicle_settings', methods=['POST'])
def update_vehicle_settings(vin):
    db = load_db()
    if vin in db.get("vehicles", {}):
        v_data = db["vehicles"][vin]
        if new_mileage := request.form.get("mileage"):
            if new_mileage.isdigit(): v_data["current_mileage"] = int(new_mileage)
        v_data["nickname"] = request.form.get("nickname", "").strip()
        v_data["ha_entity_id"] = request.form.get("ha_entity_id", "").strip()
        v_data["image_url"] = request.form.get("image_url", "").strip()
        save_db(db, sync_mqtt=False)
    return redirect(f"{get_base_path()}/vehicle/{vin}")

@app.route('/api/<vin>/update_theme', methods=['POST'])
def update_theme(vin):
    db = load_db()
    if color := request.form.get("theme_color"):
        db["vehicles"][vin]["theme_color"] = color
        save_db(db, sync_mqtt=False)
    return redirect(f"{get_base_path()}/vehicle/{vin}")

@app.route('/api/<vin>/delete_vehicle', methods=['POST'])
def delete_vehicle(vin):
    db = load_db()
    if vin in db.get("vehicles", {}):
        if mqtt_client and mqtt_client.is_connected():
            for s in db["vehicles"][vin].get("services", []): mqtt_client.publish(f"homeassistant/sensor/fleet_{vin}/{s['id']}/config", "", retain=True)
        del db["vehicles"][vin]
        save_db(db, sync_mqtt=False)
    return redirect(f"{get_base_path()}/")

@app.route('/api/<vin>/update_specs', methods=['POST'])
def update_specs(vin):
    db = load_db()
    if vin in db.get("vehicles", {}):
        specs = db["vehicles"][vin].setdefault("specs", {})
        for key in ["engine_oil", "oil_filter", "tire_size", "tire_pressure", "wiper_blades", "manual_url", "battery_date"]:
            specs[key] = request.form.get(key, "")
        save_db(db, sync_mqtt=False)
    return redirect(f"{get_base_path()}/vehicle/{vin}")

# ==========================================
# 🔧 AJAX-READY GARAGE & TORQUE ROUTES
# ==========================================
@app.route('/api/<vin>/add_torque', methods=['POST'])
def add_torque(vin):
    db = load_db()
    new_id = str(uuid.uuid4())[:8]
    db["vehicles"][vin].setdefault("torque_specs", []).append({
        "id": new_id, 
        "component": request.form.get("component", "Unknown"), 
        "torque": request.form.get("torque", ""),
        "labels": request.form.get("labels", "").strip()
    })
    save_db(db, sync_mqtt=False)
    
    # If the request comes from JS Fetch, return JSON so the modal doesn't close!
    if request.headers.get('Accept') == 'application/json':
        return jsonify({"status": "success", "id": new_id, "component": request.form.get("component", "Unknown"), "torque": request.form.get("torque", ""), "labels": request.form.get("labels", "").strip()})
    return redirect(f"{get_base_path()}/vehicle/{vin}?tab=specs")

@app.route('/api/<vin>/delete_torque/<t_id>', methods=['POST'])
def delete_torque(vin, t_id):
    db = load_db()
    db["vehicles"][vin]["torque_specs"] = [t for t in db["vehicles"][vin].get("torque_specs", []) if t.get("id") != t_id]
    save_db(db, sync_mqtt=False)
    
    if request.headers.get('Accept') == 'application/json':
        return jsonify({"status": "success"})
    return redirect(f"{get_base_path()}/vehicle/{vin}?tab=specs")

@app.route('/api/<vin>/update_torque/<t_id>', methods=['POST'])
def update_torque(vin, t_id):
    db = load_db()
    for t in db["vehicles"][vin].get("torque_specs", []):
        if t.get("id") == t_id:
            t["component"] = request.form.get("component", t["component"])
            t["torque"] = request.form.get("torque", t["torque"])
            t["labels"] = request.form.get("labels", t.get("labels", "")).strip()
            break
    save_db(db, sync_mqtt=False)
    if request.headers.get('Accept') == 'application/json':
        return jsonify({"status": "success"})
    return redirect(f"{get_base_path()}/vehicle/{vin}?tab=specs")

# Templates use asymmetric key names: "garage_parts" (plural) for parts and
# "garage_torque" (singular) for torque items. The previous implementation
# constructed the key dynamically and got both wrong (torque ended up under
# "garage_torques", part deletes hit "garage_part") — which is exactly why
# adding a torque item appeared to vanish and deleting a part did nothing.
# Map types to the correct fixed keys instead of guessing with string math.
_GARAGE_KEYS = {"part": "garage_parts", "torque": "garage_torque"}

def _normalize_garage_type(raw):
    t = (raw or "").strip().lower().rstrip("s")
    return t if t in _GARAGE_KEYS else None

@app.route('/api/<vin>/service/<service_id>/add_garage_item', methods=['POST'])
def add_garage_item(vin, service_id):
    db = load_db()
    item_type = _normalize_garage_type(request.form.get("type", ""))
    name = request.form.get("name", "").strip()
    value = request.form.get("value", "").strip()
    new_id = str(uuid.uuid4())[:8]
    saved = False

    if item_type and name and value and vin in db.get("vehicles", {}):
        key = _GARAGE_KEYS[item_type]
        for s in db["vehicles"][vin].get("services", []):
            if s["id"] == service_id:
                s.setdefault(key, []).append({"id": new_id, "name": name, "value": value})
                saved = True
                break
        if saved:
            save_db(db, sync_mqtt=False)

    if request.headers.get('Accept') == 'application/json':
        if saved:
            return jsonify({"status": "success", "id": new_id})
        return jsonify({"status": "error", "message": "Item could not be saved"}), 400
    return redirect(request.headers.get("Referer", f"{get_base_path()}/vehicle/{vin}"))

@app.route('/api/<vin>/service/<service_id>/delete_garage_item/<item_type>/<item_id>', methods=['POST'])
def delete_garage_item(vin, service_id, item_type, item_id):
    item_type = _normalize_garage_type(item_type)
    db = load_db()
    if item_type and vin in db.get("vehicles", {}):
        key = _GARAGE_KEYS[item_type]
        for s in db["vehicles"][vin].get("services", []):
            if s["id"] == service_id:
                s[key] = [i for i in s.get(key, []) if i.get("id") != item_id]
                break
        save_db(db, sync_mqtt=False)
        
    if request.headers.get('Accept') == 'application/json':
        return jsonify({"status": "success"})
    return redirect(request.headers.get("Referer", f"{get_base_path()}/vehicle/{vin}"))

# ==========================================
# ⛽ FUEL ROUTES
# ==========================================
@app.route('/api/<vin>/add_fuel', methods=['POST'])
def add_fuel(vin):
    db = load_db()
    db["vehicles"][vin].setdefault("fuel_logs", []).append({"id": str(uuid.uuid4())[:8], "date": request.form.get("date", datetime.now().strftime("%Y-%m-%d")), "cost": float(request.form.get("cost", 0) or 0)})
    save_db(db, sync_mqtt=False)
    return redirect(request.headers.get("Referer", f"{get_base_path()}/vehicle/{vin}"))

@app.route('/api/<vin>/delete_fuel/<log_id>', methods=['POST'])
def delete_fuel(vin, log_id):
    db = load_db()
    db["vehicles"][vin]["fuel_logs"] = [l for l in db["vehicles"][vin].get("fuel_logs", []) if l.get("id") != log_id]
    save_db(db, sync_mqtt=False)
    return redirect(request.headers.get("Referer", f"{get_base_path()}/vehicle/{vin}"))

# ==========================================
# 🛠️ SERVICES & LOGBOOK ROUTES
# ==========================================
@app.route('/api/add_default', methods=['POST'])
def add_default():
    db = load_db()
    db.setdefault("default_services", []).append({"id": str(uuid.uuid4())[:8], "category": request.form.get('category', 'Other').strip(), "name": request.form.get('name', 'New Service').strip(), "interval_months": int(request.form.get('interval_months', 12)), "interval_miles": int(request.form.get('interval_miles', 10000)), "parts_info": request.form.get('parts_info', '').strip()})
    save_db(db, sync_mqtt=False)
    return redirect(f"{get_base_path()}/?tab=add")

@app.route('/api/delete_default/<service_id>', methods=['POST'])
def delete_default(service_id):
    db = load_db()
    db["default_services"] = [s for s in db.get("default_services", []) if s.get("id") != service_id]
    save_db(db, sync_mqtt=False)
    return redirect(f"{get_base_path()}/?tab=add")

@app.route('/api/<vin>/add_service', methods=['POST'])
def add_service(vin):
    db = load_db()
    db["vehicles"][vin]["services"].append({"id": str(uuid.uuid4())[:8], "category": request.form.get('category', 'Other').strip(), "name": request.form.get('name', 'New Service').strip(), "interval_months": int(request.form.get('interval_months', 12)), "interval_miles": int(request.form.get('interval_miles', 10000)), "parts_info": request.form.get('parts_info', '').strip(), "last_service_miles": None, "last_service_date": None, "garage_parts": [], "garage_torque": []})
    save_db(db)
    return redirect(f"{get_base_path()}/vehicle/{vin}")

@app.route('/api/<vin>/update_service/<service_id>', methods=['POST'])
def update_service(vin, service_id):
    db = load_db()
    for s in db["vehicles"][vin]["services"]:
        if s["id"] == service_id:
            s.update({"category": request.form.get('category', s["category"]), "name": request.form.get('name', s["name"]), "interval_months": int(request.form.get('interval_months', s["interval_months"])), "interval_miles": int(request.form.get('interval_miles', s["interval_miles"])), "parts_info": request.form.get('parts_info', '').strip()})
    save_db(db)
    return redirect(f"{get_base_path()}/vehicle/{vin}")

@app.route('/api/<vin>/delete_service/<service_id>', methods=['POST'])
def delete_service(vin, service_id):
    db = load_db()
    db["vehicles"][vin]["services"] = [s for s in db["vehicles"][vin]["services"] if s["id"] != service_id]
    save_db(db)
    if mqtt_client and mqtt_client.is_connected(): mqtt_client.publish(f"homeassistant/sensor/fleet_{vin}/{service_id}/config", "", retain=True)
    return redirect(f"{get_base_path()}/vehicle/{vin}")

def log_entry_and_sync(vin, service_name, date_str, mileage, notes, cost_parts, cost_labor, db, track_interval=True):
    v_data = db["vehicles"][vin]
    # Only update current_mileage if the new mileage is higher
    if mileage > v_data.get("current_mileage", 0):
        v_data["current_mileage"] = mileage
    resolved_name = service_name
    for s in v_data["services"]:
        if s["name"].lower() == service_name.lower() or s.get("id") == service_name:
            resolved_name = s["name"]
            break
    if track_interval:
        found = False
        for s in v_data["services"]:
            if s["name"] == resolved_name:
                s.update({"last_service_miles": mileage, "last_service_date": date_str})
                found = True
                break
        if not found:
            v_data["services"].append({"id": str(uuid.uuid4())[:8], "category": "Other", "name": resolved_name, "interval_months": 12, "interval_miles": 10000, "parts_info": "", "last_service_miles": mileage, "last_service_date": date_str, "garage_parts": [], "garage_torque": []})
    v_data.setdefault("logbook", []).append({"id": str(uuid.uuid4())[:8], "date": date_str, "service": resolved_name, "mileage": mileage, "notes": notes, "cost_parts": cost_parts, "cost_labor": cost_labor})

@app.route('/api/<vin>/add_log', methods=['POST'])
def add_log(vin):
    db = load_db()
    service_name = request.form.get("service_name")
    log_entry_and_sync(vin, service_name, request.form.get("date"), int(request.form.get("mileage", 0)), request.form.get("notes", ""), float(request.form.get("cost_parts", 0) or 0), float(request.form.get("cost_labor", 0) or 0), db, track_interval=(request.form.get("action", "log_and_track") == "log_and_track"))
    save_db(db)
    if request.headers.get('Accept') == 'application/json':
        new_log = db["vehicles"][vin]["logbook"][-1]
        global_settings = db.get("global_settings", {})
        date_fmt = global_settings.get("date_format", "YYYY-MM-DD")
        updated_service = None
        for s in calculate_status(db["vehicles"][vin], global_settings):
            if s.get("id") == service_name or s.get("name", "").lower() == service_name.lower():
                s["last_service_date_display"] = format_date_filter(s.get("last_service_date_formatted", ""), date_fmt)
                s["due_date_display"] = format_date_filter(s.get("due_date_str", ""), date_fmt)
                updated_service = s
                break
        return jsonify({"status": "success", "log": new_log, "service": updated_service})
    return redirect(f"{get_base_path()}/vehicle/{vin}")

@app.route('/api/<vin>/update_log/<log_id>', methods=['POST'])
def update_log(vin, log_id):
    db = load_db()
    for log in db["vehicles"][vin]["logbook"]:
        if log.get("id") == log_id: log.update({"date": request.form.get('date', log["date"]), "service": request.form.get('service', log["service"]), "mileage": int(request.form.get('mileage', log["mileage"])), "notes": request.form.get('notes', log["notes"]), "cost_parts": float(request.form.get('cost_parts', log.get("cost_parts", 0)) or 0), "cost_labor": float(request.form.get('cost_labor', log.get("cost_labor", 0)) or 0)})
    save_db(db, sync_mqtt=False)
    return redirect(f"{get_base_path()}/vehicle/{vin}")

@app.route('/api/<vin>/delete_log/<log_id>', methods=['POST'])
def delete_log(vin, log_id):
    db = load_db()
    db["vehicles"][vin]["logbook"] = [l for l in db["vehicles"][vin]["logbook"] if l.get("id") != log_id]
    save_db(db, sync_mqtt=False)
    return redirect(f"{get_base_path()}/vehicle/{vin}")

@app.route('/api/<vin>/batch_delete_services', methods=['POST'])
def batch_delete_services(vin):
    ids = request.json.get('ids', [])
    db = load_db()
    db["vehicles"][vin]["services"] = [s for s in db["vehicles"][vin]["services"] if s["id"] not in ids]
    save_db(db)
    if mqtt_client and mqtt_client.is_connected():
        for sid in ids:
            mqtt_client.publish(f"homeassistant/sensor/fleet_{vin}/{sid}/config", "", retain=True)
    return jsonify({"status": "success", "deleted": len(ids)})

@app.route('/api/<vin>/batch_delete_logs', methods=['POST'])
def batch_delete_logs(vin):
    ids = request.json.get('ids', [])
    db = load_db()
    db["vehicles"][vin]["logbook"] = [l for l in db["vehicles"][vin]["logbook"] if l.get("id") not in ids]
    save_db(db, sync_mqtt=False)
    return jsonify({"status": "success", "deleted": len(ids)})

@app.route('/api/<vin>/batch_update_services', methods=['POST'])
def batch_update_services(vin):
    data = request.json or {}
    ids = data.get('ids', [])
    updates = data.get('updates', {})
    if not ids or not updates:
        return jsonify({"status": "error", "message": "No ids or updates"})
    db = load_db()
    for s in db["vehicles"][vin]["services"]:
        if s["id"] in ids:
            if 'category'        in updates: s['category']        = str(updates['category'])
            if 'name'            in updates: s['name']            = str(updates['name'])
            if 'parts_info'      in updates: s['parts_info']      = str(updates['parts_info'])
            if 'interval_months' in updates: s['interval_months'] = int(updates['interval_months'])
            if 'interval_miles'  in updates: s['interval_miles']  = int(updates['interval_miles'])
    save_db(db)
    return jsonify({"status": "success", "updated": len(ids)})

@app.route('/api/<vin>/batch_update_logs', methods=['POST'])
def batch_update_logs(vin):
    data = request.json or {}
    ids = data.get('ids', [])
    updates = data.get('updates', {})
    if not ids or not updates:
        return jsonify({"status": "error", "message": "No ids or updates"})
    db = load_db()
    for log in db["vehicles"][vin]["logbook"]:
        if log.get("id") in ids:
            if 'date'        in updates: log['date']        = str(updates['date'])
            if 'service'     in updates: log['service']     = str(updates['service'])
            if 'mileage'     in updates: log['mileage']     = int(updates['mileage'])
            if 'notes'       in updates: log['notes']       = str(updates['notes'])
            if 'cost_parts'  in updates: log['cost_parts']  = float(updates['cost_parts'])
            if 'cost_labor'  in updates: log['cost_labor']  = float(updates['cost_labor'])
    save_db(db, sync_mqtt=False)
    return jsonify({"status": "success", "updated": len(ids)})

@app.route('/api/<vin>/batch_update_torque', methods=['POST'])
def batch_update_torque(vin):
    data = request.json or {}
    ids = data.get('ids', [])
    updates = data.get('updates', {})
    if not ids or not updates:
        return jsonify({"status": "error", "message": "No ids or updates"})
    db = load_db()
    for t in db["vehicles"][vin].get("torque_specs", []):
        if t.get("id") in ids:
            if 'component' in updates: t['component'] = str(updates['component'])
            if 'torque'    in updates: t['torque']    = str(updates['torque'])
            if 'labels'    in updates: t['labels']    = str(updates['labels'])
    save_db(db, sync_mqtt=False)
    return jsonify({"status": "success", "updated": len(ids)})

@app.route('/api/<vin>/clear_logbook', methods=['POST'])
def clear_logbook(vin):
    db = load_db()
    if vin in db.get("vehicles", {}):
        db["vehicles"][vin]["logbook"] = []
        save_db(db, sync_mqtt=False)
    return redirect(f"{get_base_path()}/vehicle/{vin}?tab=logbook")

@app.route('/api/import_baseline_csv', methods=['POST'])
@app.route('/api/<vin>/import_baseline_csv', methods=['POST'])
def import_baseline_csv(vin=None):
    # The VIN-prefixed route exists only for backward compatibility — this
    # endpoint writes to the app-wide default_services template, not to any
    # specific vehicle, so the vin argument is intentionally ignored.
    if 'csv_file' in request.files and request.files['csv_file'].filename != '':
        db = load_db()
        new_defaults = []
        for row in csv.DictReader(StringIO(request.files['csv_file'].stream.read().decode("UTF8"), newline=None)):
            new_defaults.append({"id": str(uuid.uuid4())[:8], "category": row.get('Category', 'Other').strip(), "name": row.get('Service', 'Unknown').strip(), "interval_months": int(row.get('Interval_Months', 0)), "interval_miles": int(row.get('Interval_Miles', 0)), "parts_info": row.get('Parts_Info', '').strip()})
        if new_defaults:
            db["default_services"] = new_defaults
            save_db(db, sync_mqtt=False)
    return redirect(f"{get_base_path()}/?tab=add")

@app.route('/api/<vin>/save_as_baseline', methods=['POST'])
def save_as_baseline(vin):
    db = load_db()
    if vin in db.get("vehicles", {}):
        db["default_services"] = [{"id": str(uuid.uuid4())[:8], "category": s.get("category", "Other"), "name": s.get("name", "Unknown"), "interval_months": s.get("interval_months", 12), "interval_miles": s.get("interval_miles", 10000), "parts_info": s.get("parts_info", "")} for s in db["vehicles"][vin].get("services", [])]
        save_db(db, sync_mqtt=False)
    return redirect(f"{get_base_path()}/vehicle/{vin}")

@app.route('/api/<vin>/export_intervals')
def export_intervals(vin):
    db = load_db()
    if vin not in db.get("vehicles", {}): return redirect(f"{get_base_path()}/")
    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(['Category', 'Service', 'Interval_Months', 'Interval_Miles', 'Parts_Info'])
    for s in db["vehicles"][vin].get("services", []): writer.writerow([s.get('category', ''), s.get('name', ''), s.get('interval_months', ''), s.get('interval_miles', ''), s.get('parts_info', '')])
    return Response(si.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment;filename=intervals_{vin}.csv"})

@app.route('/api/<vin>/import_csv', methods=['POST'])
def import_csv(vin):
    if 'csv_file' in request.files:
        db = load_db()
        # Clear existing services only if checkbox is checked
        if request.form.get('clear_existing') == 'yes':
            db["vehicles"][vin]["services"] = []
        for row in csv.DictReader(StringIO(request.files['csv_file'].stream.read().decode("UTF8"), newline=None)): 
            db["vehicles"][vin]["services"].append({"id": str(uuid.uuid4())[:8], "category": row.get('Category', 'Other').strip(), "name": row.get('Service', 'Unknown').strip(), "interval_months": int(row.get('Interval_Months', 0)), "interval_miles": int(row.get('Interval_Miles', 0)), "parts_info": row.get('Parts_Info', '').strip(), "last_service_miles": None, "last_service_date": None, "garage_parts": [], "garage_torque": []})
        save_db(db)
    return redirect(f"{get_base_path()}/vehicle/{vin}")

@app.route('/api/<vin>/import_logbook', methods=['POST'])
def import_logbook(vin):
    if 'csv_file' in request.files:
        db = load_db()
        if vin not in db.get("vehicles", {}):
            return redirect(f"{get_base_path()}/")
        
        # Clear existing logbook entries only if checkbox is checked
        if request.form.get('clear_existing') == 'yes':
            db["vehicles"][vin]["logbook"] = []
        
        success_count = 0
        error_messages = []
        
        try:
            csv_content = request.files['csv_file'].stream.read().decode("UTF-8-sig")  # Handle BOM
            reader = csv.DictReader(StringIO(csv_content, newline=None))
            
            for line_num, row in enumerate(reader, start=2):  # Start at 2 (header is line 1)
                try:
                    # Normalize column names (strip whitespace, handle case variations)
                    normalized_row = {k.strip(): v for k, v in row.items() if k}
                    
                    service_name = (normalized_row.get('Service') or normalized_row.get('service') or 'Unknown').strip()
                    date_str = (normalized_row.get('Date') or normalized_row.get('date') or '').strip()
                    notes = (normalized_row.get('Notes') or normalized_row.get('notes') or '').strip()
                    
                    # Parse mileage - handle commas and various formats
                    mileage_raw = normalized_row.get('Mileage') or normalized_row.get('mileage') or '0'
                    mileage_str = str(mileage_raw).replace(',', '').strip()
                    mileage = int(float(mileage_str)) if mileage_str else 0
                    
                    # Parse costs - handle currency symbols and commas
                    parts_raw = normalized_row.get('Parts') or normalized_row.get('parts') or '0'
                    parts_str = str(parts_raw).replace('$', '').replace(',', '').strip()
                    cost_parts = float(parts_str) if parts_str else 0.0
                    
                    labor_raw = normalized_row.get('Labor') or normalized_row.get('labor') or '0'
                    labor_str = str(labor_raw).replace('$', '').replace(',', '').strip()
                    cost_labor = float(labor_str) if labor_str else 0.0
                    
                    log_entry_and_sync(vin, service_name, parse_date(date_str).strftime("%Y-%m-%d"), 
                                      mileage, notes, cost_parts, cost_labor, db, track_interval=True)
                    success_count += 1
                except Exception as e:
                    error_messages.append(f"Line {line_num}: {str(e)}")
                    continue
            
            save_db(db)
            
        except Exception as e:
            error_messages.append(f"CSV parsing error: {str(e)}")
        
        if error_messages:
            # Store errors in session or flash - for now, just print
            print(f"Import logbook errors for {vin}: {error_messages}")
    
    return redirect(f"{get_base_path()}/vehicle/{vin}")

@app.route('/api/<vin>/export_blueprint')
def export_blueprint(vin):
    """
    Export a vehicle's non-personal configuration as a shareable JSON
    blueprint. Includes interval definitions, garage items (parts + torque
    lists per service), the global torque-spec table, and the spec sheet
    fields (oil type, tire size, etc).

    Stripped on the way out: VIN, nickname, current_mileage, image_url,
    ha_entity_id, theme_color, share_token, fuel_logs, logbook, all
    last_service_* fields, and any internal ids. The output is meant to be
    pasted into a fresh vehicle as a starting template — nothing here
    identifies the owner or their personal usage history.
    """
    db = load_db()
    if vin not in db.get("vehicles", {}):
        return jsonify({"error": "vehicle not found"}), 404
    v = db["vehicles"][vin]

    def _scrub_garage(items):
        return [{"name": i.get("name", ""), "value": i.get("value", "")}
                for i in (items or [])]

    blueprint = {
        "blueprint_version": 1,
        "exported_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "year": v.get("year", ""),
        "make": v.get("make", ""),
        "model": v.get("model", ""),
        "services": [
            {
                "category": s.get("category", "Other"),
                "name": s.get("name", ""),
                "interval_months": s.get("interval_months", 0),
                "interval_miles": s.get("interval_miles", 0),
                "parts_info": s.get("parts_info", ""),
                "garage_parts": _scrub_garage(s.get("garage_parts", [])),
                "garage_torque": _scrub_garage(s.get("garage_torque", [])),
            }
            for s in v.get("services", [])
        ],
        "torque_specs": [
            {
                "component": t.get("component", ""),
                "torque": t.get("torque", ""),
                "labels": t.get("labels", ""),
            }
            for t in v.get("torque_specs", [])
        ],
        "specs": {
            key: v.get("specs", {}).get(key, "")
            for key in ["engine_oil", "oil_filter", "tire_size",
                        "tire_pressure", "wiper_blades", "manual_url"]
        },
    }

    # Build a clean filename from year/make/model; fall back to the slug.
    parts = [str(p).strip() for p in (v.get("year"), v.get("make"), v.get("model")) if str(p).strip()]
    slug = "-".join(parts) if parts else vin
    safe_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", slug)
    filename = f"blueprint-{safe_slug}.json"

    return Response(
        json.dumps(blueprint, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment;filename={filename}"},
    )


@app.route('/api/<vin>/import_blueprint', methods=['POST'])
def import_blueprint(vin):
    if 'blueprint_file' not in request.files or request.files['blueprint_file'].filename == '':
        return redirect(f"{get_base_path()}/vehicle/{vin}")
    db = load_db()
    if vin not in db.get("vehicles", {}):
        return redirect(f"{get_base_path()}/")
    try:
        bp = json.loads(request.files['blueprint_file'].stream.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return redirect(f"{get_base_path()}/vehicle/{vin}")
    if not isinstance(bp, dict) or bp.get("blueprint_version") != 1:
        return redirect(f"{get_base_path()}/vehicle/{vin}")

    v = db["vehicles"][vin]
    clear_services = request.form.get('clear_services') == 'yes'
    clear_torque = request.form.get('clear_torque') == 'yes'

    if "services" in bp:
        if clear_services:
            v["services"] = []
        for s in bp.get("services", []):
            v["services"].append({
                "id": str(uuid.uuid4())[:8],
                "category": str(s.get("category", "Other")).strip() or "Other",
                "name": str(s.get("name", "")).strip(),
                "interval_months": int(s.get("interval_months", 0)),
                "interval_miles": int(s.get("interval_miles", 0)),
                "parts_info": str(s.get("parts_info", "")).strip(),
                "last_service_miles": None,
                "last_service_date": None,
                "garage_parts": [{"id": str(uuid.uuid4())[:8], "name": str(p.get("name", "")), "value": str(p.get("value", ""))} for p in (s.get("garage_parts") or [])],
                "garage_torque": [{"id": str(uuid.uuid4())[:8], "name": str(t.get("name", "")), "value": str(t.get("value", ""))} for t in (s.get("garage_torque") or [])],
            })

    if "torque_specs" in bp:
        if clear_torque:
            v["torque_specs"] = []
        for t in bp.get("torque_specs", []):
            v.setdefault("torque_specs", []).append({
                "id": str(uuid.uuid4())[:8],
                "component": str(t.get("component", "")).strip(),
                "torque": str(t.get("torque", "")).strip(),
                "labels": str(t.get("labels", "")).strip(),
            })

    if "specs" in bp and isinstance(bp["specs"], dict):
        existing_specs = v.get("specs") or {}
        for key in ("engine_oil", "oil_filter", "tire_size", "tire_pressure", "wiper_blades", "manual_url"):
            if bp["specs"].get(key):
                existing_specs[key] = str(bp["specs"][key]).strip()
        v["specs"] = existing_specs

    save_db(db)
    return redirect(f"{get_base_path()}/vehicle/{vin}")


@app.route('/api/<vin>/export_logbook')
def export_logbook(vin):
    db = load_db()
    if vin not in db.get("vehicles", {}): return redirect(f"{get_base_path()}/")
    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(['Date', 'Service', 'Mileage', 'Notes', 'Parts', 'Labor'])
    for log in sorted(db["vehicles"][vin].get("logbook", []), key=lambda x: x.get('date', ''), reverse=True):
        # Export with consistent formatting - no currency symbols in the CSV
        writer.writerow([
            log.get('date', ''), 
            log.get('service', ''), 
            log.get('mileage', ''), 
            log.get('notes', ''), 
            f"{float(log.get('cost_parts', 0)):.2f}",
            f"{float(log.get('cost_labor', 0)):.2f}"
        ])
    return Response(si.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment;filename=logbook_{vin}.csv"})

# ==========================================
# 📚 LOCAL BLUEPRINT LIBRARY
# ==========================================

@app.route('/api/blueprint_library')
def get_blueprint_library():
    make = request.args.get('make', '')
    model = request.args.get('model', '')
    results = cbp.search_local_library(make, model)
    return jsonify(results)

@app.route('/api/blueprint_library/publish', methods=['POST'])
def publish_blueprint():
    db = load_db()
    vin = request.form.get('vin', '')
    if vin not in db.get('vehicles', {}):
        return jsonify({'status': 'error', 'message': 'Vehicle not found'}), 404
    v = db['vehicles'][vin]

    def _scrub(items):
        return [{'name': i.get('name', ''), 'value': i.get('value', '')} for i in (items or [])]

    blueprint = {
        'blueprint_version': 1,
        'exported_at': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'year': v.get('year', ''),
        'make': v.get('make', ''),
        'model': v.get('model', ''),
        'services': [{'category': s.get('category', 'Other'), 'name': s.get('name', ''), 'interval_months': s.get('interval_months', 0), 'interval_miles': s.get('interval_miles', 0), 'parts_info': s.get('parts_info', ''), 'garage_parts': _scrub(s.get('garage_parts', [])), 'garage_torque': _scrub(s.get('garage_torque', []))} for s in v.get('services', [])],
        'torque_specs': [{'component': t.get('component', ''), 'torque': t.get('torque', ''), 'labels': t.get('labels', '')} for t in v.get('torque_specs', [])],
        'specs': {k: v.get('specs', {}).get(k, '') for k in ['engine_oil', 'oil_filter', 'tire_size', 'tire_pressure', 'wiper_blades', 'manual_url']},
    }
    entry = cbp.publish_to_local_library(blueprint, vin_label=vin)
    return jsonify({'status': 'success', 'id': entry['id'], 'label': entry['label']})

@app.route('/api/blueprint_library/<bp_id>', methods=['DELETE'])
def delete_blueprint(bp_id):
    deleted = cbp.delete_from_local_library(bp_id)
    return jsonify({'status': 'success' if deleted else 'not_found'})

@app.route('/api/blueprint_library/<bp_id>/data')
def get_local_blueprint_data(bp_id):
    entry = cbp.get_local_blueprint(bp_id)
    if not entry:
        return jsonify({'error': 'not found'}), 404
    return jsonify(entry)


# ==========================================
# 🌐 COMMUNITY BLUEPRINT DATABASE
# ==========================================

@app.route('/api/community_blueprints')
def get_community_blueprints():
    make = request.args.get('make', '')
    model = request.args.get('model', '')
    try:
        results = cbp.fetch_community_index(make, model)
    except Exception:
        results = []
    return jsonify(results)

@app.route('/api/community_blueprints/<bp_id>')
def get_community_blueprint(bp_id):
    data = cbp.fetch_community_blueprint(bp_id)
    if not data:
        return jsonify({'error': 'not found'}), 404
    return jsonify(data)

@app.route('/api/community_blueprints/repo_url')
def community_repo_url():
    return jsonify({'url': cbp.get_repo_url(), 'submit_enabled': bool(cbp.COMMUNITY_SUBMIT_URL)})

@app.route('/api/community_blueprints/refresh_cache', methods=['POST'])
def refresh_community_cache():
    cbp.invalidate_cache()
    return jsonify({'status': 'ok'})

@app.route('/api/community_blueprints/contribute', methods=['POST'])
def contribute_blueprint():
    db = load_db()
    vin = request.form.get('vin', '')
    if vin not in db.get('vehicles', {}):
        return jsonify({'status': 'error', 'message': 'Vehicle not found'}), 404
    v = db['vehicles'][vin]

    def _scrub(items):
        return [{'name': i.get('name', ''), 'value': i.get('value', '')} for i in (items or [])]

    blueprint = {
        'blueprint_version': 1,
        'exported_at': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'year': v.get('year', ''),
        'make': v.get('make', ''),
        'model': v.get('model', ''),
        'services': [{'category': s.get('category', 'Other'), 'name': s.get('name', ''), 'interval_months': s.get('interval_months', 0), 'interval_miles': s.get('interval_miles', 0), 'parts_info': s.get('parts_info', ''), 'garage_parts': _scrub(s.get('garage_parts', [])), 'garage_torque': _scrub(s.get('garage_torque', []))} for s in v.get('services', [])],
        'torque_specs': [{'component': t.get('component', ''), 'torque': t.get('torque', ''), 'labels': t.get('labels', '')} for t in v.get('torque_specs', [])],
        'specs': {k: v.get('specs', {}).get(k, '') for k in ['engine_oil', 'oil_filter', 'tire_size', 'tire_pressure', 'wiper_blades', 'manual_url']},
    }
    result = cbp.submit_blueprint(blueprint)
    return jsonify(result)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
