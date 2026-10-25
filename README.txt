ESP32 Dual-Sensor IoT System (MQTT + SQLite + Predictive Analytics)
==================================================================
1. Open PowerShell in this folder (C:\IOT).
2. Run: python server.py
   - Starts the Embedded MQTT Broker on port 1883.
   - Initializes SQLite database: iot_monitor.db.
   - Launches the Web Dashboard on http://127.0.0.1:8000.
3. Open http://127.0.0.1:8000 in your browser.
4. Click “View Past Statistics” to open the date-range query page backed by SQLite SQL queries.
5. In ESP32_Temperature_Monitor.ino:
   - Configure WIFI_SSID and WIFI_PASSWORD.
   - Set MQTT_SERVER to your computer's local LAN IPv4 address (found via `ipconfig`).
   - Flash the firmware to the ESP32.

KEY UPGRADES IMPLEMENTED:
-------------------------
1. Protocol: Replaced HTTP with MQTT (Port 1883, Topic: iot/temperature/data, QoS 1).
2. Database: Replaced flat file.txt with persistent SQLite (iot_monitor.db). Historical data was automatically migrated.
3. Zero Data Loss: ESP32 features an offline circular buffer (up to 120 readings). If Wi-Fi flickers or drops, readings are cached locally and automatically flushed chronologically when connection restores.
4. Fast Mismatch Fault: Reduced from 1 hour to 5 minutes (300,000 ms). Discrepancy > 5°C triggers fault alarm after 5 minutes with LCD countdown.
5. Stagnation / Frozen Sensor Detection: If temperature does not change by at least 0.5°C over any 5-minute window, flags SENSOR FROZEN error on LCD, triggers warning LED/buzzer, and publishes alert over MQTT.
