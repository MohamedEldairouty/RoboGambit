#include <Servo.h>

Servo servo1;
Servo servo2;
Servo servo3;
Servo servo4;
Servo servo5;

String inputString = "";

int angles[5];

void setup() {
  Serial.begin(115200);

  servo1.attach(3);
  servo2.attach(5);
  servo3.attach(6);
  servo4.attach(9);
  servo5.attach(10);

  Serial.println("Ready");
}

void loop() {

  // Read incoming serial data
  while (Serial.available()) {
    char c = Serial.read();

    // End of message
    if (c == '\n') {

      parseAngles(inputString);

      // Move servos
      servo1.write(constrain(angles[0], 0, 180));
      servo2.write(constrain(angles[1], 0, 180));
      servo3.write(constrain(angles[2], 0, 180));
      servo4.write(constrain(angles[3], 0, 180));
      servo5.write(constrain(angles[4], 0, 180));

      // Debug print
      Serial.print("Angles: ");
      for (int i = 0; i < 5; i++) {
        Serial.print(angles[i]);
        Serial.print(" ");
      }
      Serial.println();

      // Clear buffer
      inputString = "";
    }
    else {
      inputString += c;
    }
  }
}

void parseAngles(String data) {

  int index = 0;

  for (int i = 0; i < 5; i++) {

    int commaIndex = data.indexOf(',', index);

    if (commaIndex == -1) {
      commaIndex = data.length();
    }

    String value = data.substring(index, commaIndex);

    angles[i] = value.toInt();

    index = commaIndex + 1;
  }
}
