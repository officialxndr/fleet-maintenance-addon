from flask import Flask, request, jsonify, render_template, redirect, Response, send_from_directory
import csv
import uuid
import requests  # <--- ADD THIS LINE
from datetime import datetime
from io import StringIO
from core import load_db, save_db, calculate_status, calculate_fuel_stats, calculate_adm, get_ha_sensors, parse_date, mqtt_client

app = Flask(__name__, static_folder='static')

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
    vin = request.form.get('vin', '').strip().upper()
    if vin:
        new_services_config = []
        if 'csv_file' in request.files and request.files['csv_file'].filename != '':
            for row in csv.DictReader(StringIO(request.files['csv_file'].stream.read().decode("UTF8"), newline=None)):
                new_services_config.append({"id": str(uuid.uuid4())[:8], "category": row.get('Category', 'Other').strip(), "name": row.get('Service', 'Unknown').strip(), "interval_months": int(row.get('Interval_Months', 0)), "interval_miles": int(row.get('Interval_Miles', 0)), "parts_info": row.get('Parts_Info', '').strip()})
        else: new_services_config = [dict(s, id=str(uuid.uuid4())[:8]) for s in db.get("default_services", [])]

        if request.form.get('update_baseline') == 'yes' and new_services_config:
            db["default_services"] = [{"id": str(uuid.uuid4())[:8], "category": s["category"], "name": s["name"], "interval_months": s["interval_months"], "interval_miles": s["interval_miles"], "parts_info": s.get("parts_info", "")} for s in new_services_config]

        db.setdefault("vehicles", {})[vin] = {"nickname": request.form.get('nickname', '').strip(), "year": request.form.get('year', ''), "make": request.form.get('make', ''), "model": request.form.get('model', ''), "current_mileage": int(request.form.get('mileage', 0)), "theme_color": "#2563eb", "ha_entity_id": request.form.get('ha_entity_id', '').strip(), "image_url": request.form.get('image_url', '').strip(), "services": [dict(s, last_service_miles=None, last_service_date=None, garage_parts=[], garage_torque=[]) for s in new_services_config], "logbook": [], "specs": {"battery_date": ""}, "fuel_logs": [], "torque_specs": [], "share_token": str(uuid.uuid4())}
        save_db(db)
        return redirect(f"{get_base_path()}/vehicle/{vin}")
    return redirect(f"{get_base_path()}/")

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
        return jsonify({"status": "success", "id": new_id})
    return redirect(f"{get_base_path()}/vehicle/{vin}?tab=specs")

@app.route('/api/<vin>/delete_torque/<t_id>', methods=['POST'])
def delete_torque(vin, t_id):
    db = load_db()
    db["vehicles"][vin]["torque_specs"] = [t for t in db["vehicles"][vin].get("torque_specs", []) if t.get("id") != t_id]
    save_db(db, sync_mqtt=False)
    
    if request.headers.get('Accept') == 'application/json':
        return jsonify({"status": "success"})
    return redirect(f"{get_base_path()}/vehicle/{vin}?tab=specs")

@app.route('/api/<vin>/service/<service_id>/add_garage_item', methods=['POST'])
def add_garage_item(vin, service_id):
    db = load_db()
    raw_type = request.form.get("type", "").strip().lower()
    # Normalize so both "part" and "parts" map to the correct "garage_parts" key
    item_type = raw_type.rstrip("s")  # "parts"->"part", "torque"->"torque", "part"->"part"
    name = request.form.get("name", "").strip()
    value = request.form.get("value", "").strip()
    new_id = str(uuid.uuid4())[:8]
    saved = False

    if name and value and item_type and vin in db.get("vehicles", {}):
        for s in db["vehicles"][vin].get("services", []):
            if s["id"] == service_id:
                target_list = s.setdefault(f"garage_{item_type}s", [])
                target_list.append({"id": new_id, "name": name, "value": value})
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
    db = load_db()
    if vin in db.get("vehicles", {}):
        for s in db["vehicles"][vin].get("services", []):
            if s["id"] == service_id:
                s[f"garage_{item_type}"] = [i for i in s.get(f"garage_{item_type}", []) if i["id"] != item_id]
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
    log_entry_and_sync(vin, request.form.get("service_name"), request.form.get("date"), int(request.form.get("mileage", 0)), request.form.get("notes", ""), float(request.form.get("cost_parts", 0) or 0), float(request.form.get("cost_labor", 0) or 0), db, track_interval=(request.form.get("action", "log_and_track") == "log_and_track"))
    save_db(db)
    if request.headers.get('Accept') == 'application/json':
        new_log = db["vehicles"][vin]["logbook"][-1]
        return jsonify({"status": "success", "log": new_log})
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

@app.route('/api/<vin>/import_baseline_csv', methods=['POST'])
def import_baseline_csv(vin):
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
        for row in csv.DictReader(StringIO(request.files['csv_file'].stream.read().decode("UTF8"), newline=None)): 
            db["vehicles"][vin]["services"].append({"id": str(uuid.uuid4())[:8], "category": row.get('Category', 'Other').strip(), "name": row.get('Service', 'Unknown').strip(), "interval_months": int(row.get('Interval_Months', 0)), "interval_miles": int(row.get('Interval_Miles', 0)), "parts_info": row.get('Parts_Info', '').strip(), "last_service_miles": None, "last_service_date": None, "garage_parts": [], "garage_torque": []})
        save_db(db)
    return redirect(f"{get_base_path()}/vehicle/{vin}")

@app.route('/api/<vin>/import_logbook', methods=['POST'])
def import_logbook(vin):
    if 'csv_file' in request.files:
        db = load_db()
        for row in csv.DictReader(StringIO(request.files['csv_file'].stream.read().decode("UTF8"), newline=None)): 
            log_entry_and_sync(vin, row.get('Service', 'Unknown').strip(), parse_date(row.get('Date', '')).strftime("%Y-%m-%d"), int(row.get('Mileage', 0) or 0), row.get('Notes', '').strip(), float(row.get('Parts', 0) or 0), float(row.get('Labor', 0) or 0), db, track_interval=True)
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
        writer.writerow([log.get('date', ''), log.get('service', ''), log.get('mileage', ''), log.get('notes', ''), log.get('cost_parts', 0), log.get('cost_labor', 0)])
    return Response(si.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment;filename=logbook_{vin}.csv"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)