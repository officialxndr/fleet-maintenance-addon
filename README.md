# Fleet Maintenance

A powerful, self-hosted personal car management system and vehicle tracking dashboard. Designed to go far beyond a simple logbook, this app features predictive maintenance math, job-specific "Garage Mode" toolcards, Home Assistant intelligence, and secure read-only guest sharing.

Whether you run it seamlessly as a Home Assistant Add-on or as a Standalone Docker Container, Fleet Maintenance gives you complete control over your garage's data.

## ✨ Key Features

* Intelligent Interval Tracking: Tracks maintenance based on both Time (Months) and Distance (Miles/Km). Uses your Average Daily Mileage (ADM) to predict exactly when a service will be due.
* Garage Mode: Don't dig through massive manuals. Click on a specific job (e.g., "Engine Oil & Filter") to instantly see only the required parts, fluid capacities, and specific Torque Specs for that exact job.
* Smart AI Import/Export: Built-in "Copy AI Prompt" buttons that secretly scrape your database's vocabulary to ensure external AI models generate perfectly formatted, auto-linking CSVs.
Total Cost of Ownership (TCO) & Fuel Tracking: Keep track of every dollar spent on parts, labor, and fuel.
* Read-Only Guest Links: Generate a secure, passwordless URL to share with family members so they can view upcoming maintenance or log fuel without altering your intervals or deleting data.
* Home Assistant Integration:
* Live Mileage Sync: Automatically pulls live odometer readings from HA sensors.
* Battery Intelligence: Monitors your local outdoor temperature via HA. If it drops below zero and your battery is over 4 years old, the app triggers a high-risk warning.
* MQTT Discovery: Pushes your vehicle's upcoming maintenance status back into Home Assistant as native sensors.


## 🚀 Installation Option A: Home Assistant OS (Add-on)

If you run Home Assistant OS or Supervised, you can install this app directly from the Add-on Store.

1. Open your Home Assistant dashboard.
2. Navigate to Settings > Add-ons > Add-on Store.
3. Click the three dots (`...`) in the top right corner and select Repositories.
4. Add the URL to this repository:
   `https://github.com/officialxndr/fleet-maintenance-addon`
5. Click Add, then close the modal.
6. Refresh the page, scroll down, and find Fleet Maintenance.
7. Click Install.
8. Go to the Configuration tab to enter your HA API Token and MQTT credentials (optional).
9. Click Start and select Open Web UI.



## 🐳 Installation Option B: Standalone Docker

If you prefer to run this via Unraid, a Raspberry Pi, or a standard Linux server, you can deploy it instantly using Docker Compose.

Create a `docker-compose.yml` file:

```yaml
version: '3.8'

services:
  fleet-maintenance:
    image: ghcr.io/[YOUR_GITHUB_USERNAME]/[YOUR_REPO_NAME]:latest
    container_name: fleet-maintenance
    build: .
    restart: unless-stopped
    ports:
      - "5000:5000"
    volumes:
      # Maps local data to prevent data loss upon container updates
      - ./fleet_data:/app/data
    environment:
      # Optional: Remove these if you do not use Home Assistant/MQTT
      - HA_URL=[http://192.168.1.](http://192.168.1.)XXX:8123
      - HA_TOKEN=your_long_lived_access_token_here
      - MQTT_BROKER=192.168.1.XXX
      - MQTT_PORT=1883
      - MQTT_USER=your_mqtt_username
      - MQTT_PASS=your_mqtt_password
```

Run the container:

```
Bash
docker-compose up -d
Access the app at http://[YOUR_SERVER_IP]:5000.
```


🛠️ Coming Soon
Community Blueprints: A global GitHub repository of vehicle-specific configurations. Browse your Make, Model, and Year to instantly import community-verified maintenance intervals, part numbers, and torque specs directly into your app.



⚠️ Legal Disclaimer
USE AT YOUR OWN RISK. This application is provided "as is" and "as available", without warranty of any kind, express or implied.

Automotive maintenance is inherently dangerous. The creator(s) and contributor(s) of this software are not responsible or liable for any property damage, mechanical failure, snapped bolts, missed maintenance, injury, or death that may occur from using this application.

This app allows you to store and view Torque Specifications, part numbers, and maintenance intervals, but it is the user's sole responsibility to verify the accuracy of all data against official factory service manuals. Do not rely solely on community templates, AI-generated outputs, or data entered into this application. Always verify torque specs and procedures with a certified professional or official documentation before applying a wrench to your vehicle.