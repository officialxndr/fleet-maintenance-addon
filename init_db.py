# init_db.py
import csv
import re
import json
import os

file_path = "Jeep Renegade Maintenance Dashboard - Summary.csv"
db_path = "database.json"

database = {
    "current_mileage": 118000,
    "services": [],
    "logbook": []
}

with open(file_path, 'r', encoding='utf-8') as f:
    reader = csv.reader(f)
    for i, row in enumerate(reader):
        if i <= 11 or len(row) < 11: 
            continue # Skip headers and empty rows
            
        category, service_name = row[1].strip(), row[2].strip()
        interval_str, last_date = row[3].strip(), row[4].strip()
        last_miles_str = row[5].replace(',', '').strip()
        
        if not service_name or service_name == 'Service':
            continue
            
        # Parse the '1y/15k miles' format
        match = re.match(r'(\d+)y/(\d+)k miles', interval_str)
        i_months = int(match.group(1)) * 12 if match else 0
        i_miles = int(match.group(2)) * 1000 if match else 0
        last_miles = int(last_miles_str) if last_miles_str.isdigit() else 0
        
        database["services"].append({
            "id": str(i),
            "category": category,
            "name": service_name,
            "interval_months": i_months,
            "interval_miles": i_miles,
            "last_service_miles": last_miles,
            "last_service_date": last_date
        })

with open(db_path, 'w') as f:
    json.dump(database, f, indent=2)

print("Database successfully initialized from CSV!")