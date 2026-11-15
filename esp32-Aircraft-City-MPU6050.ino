#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>

// ESP32 DevKit I2C pins. Change these for another board if needed.
constexpr int SDA_PIN = 21;
constexpr int SCL_PIN = 22;
constexpr unsigned long SAMPLE_MS = 20;  // 50 updates per second
constexpr float ROLL_DIRECTION = -1.0f;  // Flip left/right steering

Adafruit_MPU6050 mpu;
float filteredRoll = 0.0f;
float filteredPitch = 0.0f;
float centerRoll = 0.0f;
float centerPitch = 0.0f;
float yawRate = 0.0f;
float yawBias = 0.0f;
float shock = 0.0f;
unsigned long lastSample = 0;
bool initialized = false;

void sampleTilt() {
  sensors_event_t accel, gyro, temperature;
  mpu.getEvent(&accel, &gyro, &temperature);

  const float ax = accel.acceleration.x;
  const float ay = accel.acceleration.y;
  const float az = accel.acceleration.z;
  const float magnitude = sqrtf(ax * ax + ay * ay + az * az);
  shock = fabsf(magnitude - 9.81f);
  yawRate = gyro.gyro.z * 180.0f / PI;
  const float roll = atan2f(ay, az) * 180.0f / PI;
  const float pitch = atan2f(-ax, sqrtf(ay * ay + az * az)) * 180.0f / PI;

  if (!initialized) {
    filteredRoll = roll;
    filteredPitch = pitch;
    initialized = true;
  } else {
    // Smooth hand jitter without adding much control lag.
    filteredRoll += 0.25f * (roll - filteredRoll);
    filteredPitch += 0.25f * (pitch - filteredPitch);
  }
}

void setup() {
  Serial.begin(115200);
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);

  if (!mpu.begin()) {
    Serial.println("ERROR,MPU6050 not found; check 3.3V/GND/SDA/SCL");
    while (true) delay(1000);
  }

  mpu.setAccelerometerRange(MPU6050_RANGE_4_G);
  mpu.setGyroRange(MPU6050_RANGE_500_DEG);
  mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);

  // Hold the controller in its neutral position while it powers up.
  float yawSum = 0.0f;
  for (int i = 0; i < 30; ++i) {
    sampleTilt();
    yawSum += yawRate;
    delay(20);
  }
  centerRoll = filteredRoll;
  centerPitch = filteredPitch;
  yawBias = yawSum / 30.0f;
  Serial.println("READY,MPU6050");
}

void loop() {
  while (Serial.available()) {
    const char command = Serial.read();
    if (command == 'C' || command == 'c') {
      centerRoll = filteredRoll;
      centerPitch = filteredPitch;
      yawBias = yawRate;
      Serial.println("CENTERED");
    }
  }

  const unsigned long now = millis();
  if (now - lastSample < SAMPLE_MS) return;
  lastSample = now;
  sampleTilt();

  const float rollDelta = ROLL_DIRECTION * (filteredRoll - centerRoll);
  const float pitchDelta = filteredPitch - centerPitch;

  // Legacy packet keeps Skyline Flight, Orbital Salvage, and Neon City Escape working.
  Serial.print("IMU,");
  Serial.print(rollDelta, 2);
  Serial.print(',');
  Serial.println(pitchDelta, 2);

  // Full motion packet: tilt, twist rate (degrees/s), and shake strength (m/s^2).
  Serial.print("MOTION,");
  Serial.print(rollDelta, 2);
  Serial.print(',');
  Serial.print(pitchDelta, 2);
  Serial.print(',');
  Serial.print(yawRate - yawBias, 2);
  Serial.print(',');
  Serial.println(shock, 2);
}
