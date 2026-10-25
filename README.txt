ESP32 Temperature Dashboard
===========================
1. Extract this folder.
2. Open PowerShell in the folder.
3. Run: py server.py  (or python server.py)
4. Open http://127.0.0.1:8000 in your browser.
5. Click “View Past Statistics” to open the separate date-range page. Select From date and To date, then click “Show Statistics” to see temperature and humidity graphs.
6. The statistics page loads readings already saved in file.txt. The default range is the last 7 days through today.
7. Find the computer IPv4 address using `ipconfig`.
8. Add an ESP32 HTTP POST to http://COMPUTER-IP:8000/api/data.
   JSON body example:
   {"temp_dht":30.2,"temp_ds":30.5,"humidity":62.0}
9. Each POST adds one JSON record to file.txt. The dashboard refreshes every 5 seconds.
The date/time is recorded by the computer in Asia/Kolkata time.
HTML alone cannot silently append to a local file; server.py performs the saving.
Keep ESP32 and computer on the same Wi-Fi. Allow Python on private networks if Windows Firewall asks.
Do not expose this development server to the public internet.
10. Live dashboard prediction: when both sensors show a sustained upward trend over recent readings, a prediction card appears with an approximate time until both sensors reach 45°C. If temperatures are stable, falling, readings are stale, or there are too few recent samples, the prediction is hidden. The forecast is a linear trend estimate and is not a safety guarantee. The ESP32's existing red-alarm logic remains unchanged.
