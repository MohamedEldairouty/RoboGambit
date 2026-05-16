/*
 * RoboGambit - 5 Servo Robotic Arm Controller
 * Serial command format: S1 155  (servo number + space + angle)
 * Speed command:         SPEED 10 (delay in ms per degree, higher = slower)
 *
 * Servo wiring:
 *   S1 (D3)  = gripper           range [130, 180]
 *   S2 (D5)  = shoulder          range [ 40, 180]
 *   S3 (D11) = elbow             range [  0,  60]
 *   S4 (D9)  = wrist             range [ 50, 120]
 *   S5 (D10) = base rotation     range [  0, 180]
 *
 * Baud: 9600
 */

#include <Servo.h>

// ── Speed Control ──────────────────────────────────────────────
int moveDelay = 15;  // ms per degree step (increase = slower, decrease = faster)

// ── Servo Definitions ──────────────────────────────────────────
const int NUM_SERVOS = 5;

Servo servos[NUM_SERVOS];
const int servoPins[NUM_SERVOS] = {3, 5, 11, 9, 10};

//                                S1   S2   S3   S4   S5
const int servoMin[NUM_SERVOS] = {130,  40,   0,  50,   0};
const int servoMax[NUM_SERVOS] = {180, 180,  60, 120, 180};

// Start positions: S1 starts at 180, others at midpoint of their range
int currentPos[NUM_SERVOS];

// ── Serial Parsing ─────────────────────────────────────────────
char cmdBuffer[32];
int bufIndex = 0;

void setup() {
  Serial.begin(9600);

  // Calculate start positions
  for (int i = 0; i < NUM_SERVOS; i++) {
    currentPos[i] = (servoMin[i] + servoMax[i]) / 2;  // midpoint
  }
  currentPos[0] = 180;  // Servo 1 override: start at 180

  // Attach and move to start positions
  for (int i = 0; i < NUM_SERVOS; i++) {
    servos[i].attach(servoPins[i]);
    servos[i].write(currentPos[i]);
  }

  delay(500);
  printStatus();
  Serial.println(F("Commands:  S1 155 | S2 80 | SPEED 20"));
  Serial.println(F("Ready.\n"));
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (bufIndex > 0) {
        cmdBuffer[bufIndex] = '\0';
        parseCommand(cmdBuffer);
        bufIndex = 0;
      }
    } else if (bufIndex < 31) {
      cmdBuffer[bufIndex++] = c;
    }
  }
}

// ── Command Parser ─────────────────────────────────────────────
void parseCommand(char* cmd) {
  // SPEED command
  if (cmd[0] == 'S' && cmd[1] == 'P') {
    char* valStr = strchr(cmd, ' ');
    if (valStr) {
      int val = atoi(valStr + 1);
      if (val >= 1 && val <= 100) {
        moveDelay = val;
        Serial.print(F("Speed set to "));
        Serial.print(moveDelay);
        Serial.println(F(" ms/deg"));
      } else {
        Serial.println(F("Error: SPEED range 1-100"));
      }
    }
    return;
  }

  // STATUS command
  if (cmd[0] == 'S' && cmd[1] == 'T') {
    printStatus();
    return;
  }

  // Servo command: S1 155
  if (cmd[0] == 'S' && cmd[1] >= '1' && cmd[1] <= '5') {
    int idx = cmd[1] - '1';  // 0-based index
    char* valStr = strchr(cmd, ' ');
    if (valStr) {
      int target = atoi(valStr + 1);

      // Clamp to limits
      if (target < servoMin[idx]) target = servoMin[idx];
      if (target > servoMax[idx]) target = servoMax[idx];

      Serial.print(F("S"));
      Serial.print(idx + 1);
      Serial.print(F(": "));
      Serial.print(currentPos[idx]);
      Serial.print(F(" -> "));
      Serial.println(target);

      smoothMove(idx, target);

      Serial.println(F("Done."));
    } else {
      Serial.println(F("Error: format is S1 155"));
    }
    return;
  }

  Serial.println(F("Unknown command."));
}

// ── Smooth Movement ────────────────────────────────────────────
void smoothMove(int idx, int target) {
  int pos = currentPos[idx];
  int step = (target > pos) ? 1 : -1;

  while (pos != target) {
    pos += step;
    servos[idx].write(pos);
    delay(moveDelay);
  }

  currentPos[idx] = target;
}

// ── Status Print ───────────────────────────────────────────────
void printStatus() {
  Serial.println(F("──── Servo Status ────"));
  for (int i = 0; i < NUM_SERVOS; i++) {
    Serial.print(F("  S"));
    Serial.print(i + 1);
    Serial.print(F(": "));
    Serial.print(currentPos[i]);
    Serial.print(F("°  ["));
    Serial.print(servoMin[i]);
    Serial.print(F("-"));
    Serial.print(servoMax[i]);
    Serial.println(F("]"));
  }
  Serial.print(F("  Speed: "));
  Serial.print(moveDelay);
  Serial.println(F(" ms/deg"));
  Serial.println(F("──────────────────────"));
}
