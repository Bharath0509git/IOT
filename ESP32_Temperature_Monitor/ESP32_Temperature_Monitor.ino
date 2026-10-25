#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <DHT.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <math.h>

// ========== WIFI SETTINGS ==========
const char* WIFI_SSID = "vivo X300";
const char* WIFI_PASSWORD = "88888888";

// Replace with your computer's IPv4 address from ipconfig
const char* SERVER_URL =
    "http://10.217.145.101:8000/api/data";

// ========== SENSOR PINS ==========
#define DHTPIN 4
#define DHTTYPE DHT11
#define DS18B20_PIN 22

// ========== OUTPUT PINS ==========
#define RELAY_PIN 23
#define BUZZER_PIN 27

#define YELLOW_LED 21
#define RED_LED 25
#define GREEN_LED 26

// ========== LCD PINS ==========
#define LCD_SDA 18
#define LCD_SCL 19

LiquidCrystal_I2C lcd(0x27, 16, 2);

DHT dht(DHTPIN, DHTTYPE);
OneWire oneWire(DS18B20_PIN);
DallasTemperature ds18b20(&oneWire);

// ========== TEMPERATURE THRESHOLDS ==========
#define HIGH_TEMP 32.0
#define RESET_TEMP 31.0
#define VERY_HIGH_TEMP 45.0
#define MAX_DIFFERENCE 5.0

// ========== TIMING ==========
const unsigned long FAULT_CONFIRM_TIME = 3600000UL;
const unsigned long BLINK_INTERVAL = 500UL;
const unsigned long READ_INTERVAL = 2500UL;
const unsigned long HIGH_TEMP_CONFIRM_TIME = 10000UL;

const unsigned long WARNING_FLASH_INTERVAL = 2500UL;
const unsigned long WARNING_FLASH_DURATION = 150UL;

const unsigned long BUZZER_START_DELAY = 2000UL;
const unsigned long BUZZER_BEEP_INTERVAL = 1000UL;
const unsigned long BUZZER_BEEP_DURATION = 200UL;

const unsigned long WIFI_RETRY_INTERVAL = 10000UL;

// ========== SYSTEM STATES ==========
bool highTemperature = false;
bool veryHighTemperature = false;
bool faultConfirmed = false;
bool sensorReadingFailed = false;
bool mismatchActive = false;
bool highTempTimerActive = false;

bool redLedState = false;
bool yellowLedState = false;
bool buzzerState = false;
bool buzzerWarningActive = false;

unsigned long mismatchStartTime = 0;
unsigned long highTempStartTime = 0;
unsigned long previousReadTime = 0;
unsigned long previousBlinkTime = 0;

unsigned long warningFlashStart = 0;
unsigned long previousWarningFlash = 0;

unsigned long buzzerWarningStart = 0;
unsigned long previousBuzzerTime = 0;
unsigned long previousWiFiAttempt = 0;

// ========== RELAY FUNCTIONS ==========
// Your requested relay control functions
void relayOn() {
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, LOW);  // LOW = relay ON
}

void relayOff() {
  pinMode(RELAY_PIN, INPUT);     // Released = relay OFF
}

// ========== LCD FUNCTION ==========
void showLCD(const char* line1, const char* line2) {
  lcd.setCursor(0, 0);
  lcd.print("                ");
  lcd.setCursor(0, 0);
  lcd.print(line1);

  lcd.setCursor(0, 1);
  lcd.print("                ");
  lcd.setCursor(0, 1);
  lcd.print(line2);
}

// ========== WIFI CONNECTION ==========
void maintainWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  unsigned long now = millis();

  if (previousWiFiAttempt == 0 ||
      now - previousWiFiAttempt >= WIFI_RETRY_INTERVAL) {

    previousWiFiAttempt = now;

    Serial.println("Connecting to WiFi...");
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  }
}

// ========== SEND READINGS TO COMPUTER ==========
void sendToDashboard(float tempDHT,
                     float tempDS,
                     float humidity) {

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Dashboard: WiFi disconnected; reading not sent.");
    return;
  }

  HTTPClient http;
  http.setTimeout(1500);

  if (!http.begin(SERVER_URL)) {
    Serial.println("Dashboard: HTTP initialization failed.");
    return;
  }

  http.addHeader("Content-Type", "application/json");

  String json = "{\"temp_dht\":" + String(tempDHT, 2) +
                ",\"temp_ds\":" + String(tempDS, 2) +
                ",\"humidity\":" + String(humidity, 2) + "}";

  int responseCode = http.POST(json);
  Serial.print("Dashboard HTTP status: ");
  Serial.println(responseCode);

  String response = http.getString();
  Serial.print("Dashboard response: ");
  Serial.println(response);

  if (responseCode >= 200 && responseCode < 300) {
    Serial.println("Dashboard: Reading saved successfully.");
  } else {
    Serial.print("Dashboard HTTP error: ");
    Serial.println(responseCode);
  }

  http.end();
}

// ========== SETUP ==========
void setup() {
  Serial.begin(115200);

  // Initialize relay in OFF state
  relayOff();

  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);

  pinMode(GREEN_LED, OUTPUT);
  pinMode(YELLOW_LED, OUTPUT);
  pinMode(RED_LED, OUTPUT);

  digitalWrite(GREEN_LED, LOW);
  digitalWrite(YELLOW_LED, LOW);
  digitalWrite(RED_LED, LOW);

  dht.begin();
  ds18b20.begin();

  Wire.begin(LCD_SDA, LCD_SCL);
  lcd.init();
  lcd.backlight();

  showLCD("Temperature", "Starting...");
  delay(1000);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  previousWiFiAttempt = millis();

  Serial.println("Dual Sensor Monitoring Started");
  Serial.println("Connecting to WiFi...");
}

// ========== MAIN LOOP ==========
void loop() {
  unsigned long now = millis();

  maintainWiFi();

  // Read sensors every 2.5 seconds
  if (previousReadTime == 0 ||
      now - previousReadTime >= READ_INTERVAL) {

    previousReadTime = now;

    float humidity = dht.readHumidity();
    float tempDHT = dht.readTemperature();

    ds18b20.requestTemperatures();
    float tempDS = ds18b20.getTempCByIndex(0);

    bool dhtValid =
        !isnan(tempDHT) &&
        !isnan(humidity) &&
        tempDHT >= 0.0 &&
        tempDHT <= 50.0 &&
        humidity >= 0.0 &&
        humidity <= 100.0;

    bool dsValid =
        tempDS != DEVICE_DISCONNECTED_C &&
        isfinite(tempDS) &&
        tempDS >= -55.0 &&
        tempDS <= 125.0;

    Serial.println("\n--- Sensor Readings ---");

    if (dhtValid) {
      Serial.print("DHT11: ");
      Serial.print(tempDHT);
      Serial.print(" C | Humidity: ");
      Serial.print(humidity);
      Serial.println(" %");
    } else {
      Serial.println("SENSOR FAULT: DHT11 reading invalid!");
    }

    if (dsValid) {
      Serial.print("DS18B20: ");
      Serial.print(tempDS);
      Serial.println(" C");
    } else {
      Serial.println("SENSOR FAULT: DS18B20 reading invalid!");
    }

    // Save valid sensor readings to the dashboard.
    // The computer records the date and time.
    if (dhtValid && dsValid) {
      sendToDashboard(tempDHT, tempDS, humidity);
    }

    // ========== INVALID SENSOR READINGS ==========
    if (!dhtValid || !dsValid) {
      sensorReadingFailed = true;
      faultConfirmed = true;
      mismatchActive = false;
      highTempTimerActive = false;

      highTemperature = false;
      veryHighTemperature = false;

      Serial.println("SENSOR FAULT: Check connections!");
      showLCD("SENSOR FAULT!", "Check connections");
    }

    else {
      sensorReadingFailed = false;

      float difference = fabs(tempDHT - tempDS);

      Serial.print("Temperature difference: ");
      Serial.print(difference);
      Serial.println(" C");

      // ========== VERY HIGH TEMPERATURE ==========
      // Both valid sensors must exceed 45 C.
      veryHighTemperature =
          (tempDHT > VERY_HIGH_TEMP &&
           tempDS > VERY_HIGH_TEMP);

      if (veryHighTemperature) {
        highTemperature = true;
        highTempTimerActive = false;

        Serial.println("VERY HIGH TEMPERATURE!");
        Serial.println("FAN ON - RED LED BLINKING!");
      }

      // ========== SENSOR MISMATCH ==========
      if (difference > MAX_DIFFERENCE) {

        if (!mismatchActive) {
          mismatchActive = true;
          mismatchStartTime = now;
          faultConfirmed = false;
        }

        Serial.println("SENSOR FAULT: Sensor mismatch!");

        highTempTimerActive = false;

        if (!veryHighTemperature) {
          highTemperature = false;
        }

        if (now - mismatchStartTime >=
            FAULT_CONFIRM_TIME) {

          faultConfirmed = true;

          Serial.println("SENSOR FAULT: Mismatch over one hour!");

          showLCD("SENSOR FAULT!", "Mismatch > 1 hour");
        }

        else {
          unsigned long elapsed =
              (now - mismatchStartTime) / 1000UL;

          unsigned long remaining =
              (FAULT_CONFIRM_TIME / 1000UL) - elapsed;

          char line2[17];

          snprintf(line2, sizeof(line2),
                   "Wait: %lu min",
                   (remaining + 59UL) / 60UL);

          showLCD("SENSOR FAULT!", line2);
        }
      }

      // ========== SENSOR READINGS AGREE ==========
      else {
        mismatchActive = false;
        faultConfirmed = false;

        // Both sensors above 32 C starts confirmation timer.
        if (tempDHT > HIGH_TEMP &&
            tempDS > HIGH_TEMP) {

          if (!highTempTimerActive &&
              !highTemperature) {

            highTempTimerActive = true;
            highTempStartTime = now;
          }

          if (highTempTimerActive &&
              now - highTempStartTime >=
              HIGH_TEMP_CONFIRM_TIME) {

            highTemperature = true;
            highTempTimerActive = false;

            Serial.println("HIGH TEMPERATURE ALERT!");
          }
        }

        else {
          highTempTimerActive = false;

          // Reset only when BOTH sensors fall below 31 C.
          if (tempDHT < RESET_TEMP &&
              tempDS < RESET_TEMP) {

            highTemperature = false;
          }
        }

        char line1[17];
        char line2[17];

        snprintf(line1, sizeof(line1),
                 "DHT:%4.1f DS:%4.1f",
                 tempDHT, tempDS);

        snprintf(line2, sizeof(line2),
                 "Humidity:%2.0f%%",
                 humidity);

        if (veryHighTemperature) {
          showLCD("VERY HIGH TEMP!", "FAN ON - DANGER");
        }

        else if (highTemperature) {
          showLCD("HIGH TEMP ALERT", line1);
          Serial.println("HIGH TEMPERATURE ALERT!");
        }

        else if (highTempTimerActive) {
          showLCD("TEMP RISING", line1);
          Serial.println("TEMPERATURE RISING");
        }

        else {
          showLCD(line1, line2);
          Serial.println("Temperature monitoring normal.");
        }
      }

      // Critical temperature display takes priority.
      if (veryHighTemperature) {
        showLCD("VERY HIGH TEMP!", "FAN ON - DANGER");
      }
    }
  }

  // ========== LED CONTROL ==========

  // Critical temperature: red LED blinks
  if (veryHighTemperature) {
    digitalWrite(GREEN_LED, LOW);
    digitalWrite(YELLOW_LED, LOW);

    if (now - previousBlinkTime >= BLINK_INTERVAL) {
      previousBlinkTime = now;
      redLedState = !redLedState;
      digitalWrite(RED_LED, redLedState);
    }
  }

  // Invalid reading or confirmed sensor fault
  else if (faultConfirmed || sensorReadingFailed) {
    digitalWrite(GREEN_LED, LOW);
    digitalWrite(YELLOW_LED, LOW);

    if (now - previousBlinkTime >= BLINK_INTERVAL) {
      previousBlinkTime = now;
      redLedState = !redLedState;
      digitalWrite(RED_LED, redLedState);
    }
  }

  // Sensor mismatch: short yellow flash every 2.5 seconds
  else if (mismatchActive) {
    digitalWrite(GREEN_LED, LOW);
    digitalWrite(RED_LED, LOW);

    if (!yellowLedState &&
        now - previousWarningFlash >=
        WARNING_FLASH_INTERVAL) {

      previousWarningFlash = now;
      warningFlashStart = now;
      yellowLedState = true;

      digitalWrite(YELLOW_LED, HIGH);
    }

    if (yellowLedState &&
        now - warningFlashStart >=
        WARNING_FLASH_DURATION) {

      yellowLedState = false;
      digitalWrite(YELLOW_LED, LOW);
    }
  }

  // Normal or high-temperature warning
  else {
    bool yellowOn =
        highTemperature || highTempTimerActive;

    digitalWrite(GREEN_LED, yellowOn ? LOW : HIGH);
    digitalWrite(YELLOW_LED, yellowOn ? HIGH : LOW);
    digitalWrite(RED_LED, LOW);

    redLedState = false;
    yellowLedState = yellowOn;
  }

  // ========== RELAY / FAN CONTROL ==========

  if (veryHighTemperature) {
    // Critical temperature overrides sensor mismatch.
    relayOn();
  }

  else if (highTemperature &&
           !faultConfirmed &&
           !sensorReadingFailed &&
           !mismatchActive) {

    relayOn();
  }

  else {
    relayOff();
  }

  // ========== BUZZER CONTROL ==========

  // Red alert: continuous buzzer
  bool redAlert =
      veryHighTemperature ||
      faultConfirmed ||
      sensorReadingFailed;

  // Yellow warning: delayed intermittent beeping
  bool yellowWarning =
      highTemperature ||
      highTempTimerActive ||
      mismatchActive;

  if (redAlert) {
    digitalWrite(BUZZER_PIN, HIGH);
    buzzerState = true;
    buzzerWarningActive = false;
  }

  else if (yellowWarning) {

    if (!buzzerWarningActive) {
      buzzerWarningActive = true;
      buzzerWarningStart = now;
      previousBuzzerTime = now;
      buzzerState = false;

      digitalWrite(BUZZER_PIN, LOW);
    }

    // Wait 2 seconds before starting warning beeps
    if (now - buzzerWarningStart >=
        BUZZER_START_DELAY) {

      // Begin a short beep every second
      if (!buzzerState &&
          now - previousBuzzerTime >=
          BUZZER_BEEP_INTERVAL) {

        buzzerState = true;
        previousBuzzerTime = now;

        digitalWrite(BUZZER_PIN, HIGH);
      }

      // End each beep after 200 milliseconds
      if (buzzerState &&
          now - previousBuzzerTime >=
          BUZZER_BEEP_DURATION) {

        buzzerState = false;
        digitalWrite(BUZZER_PIN, LOW);
      }
    }
  }

  else {
    // Normal state: buzzer OFF
    digitalWrite(BUZZER_PIN, LOW);

    buzzerState = false;
    buzzerWarningActive = false;
  }
}