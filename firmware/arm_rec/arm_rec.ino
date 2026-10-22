// arm_rec.ino - AX-12A 5-axis LEADER-FOLLOWER + DATA RECORDING + PC CONTROL (OpenCM9.04)
//
// Normally driven by tools/record.py; can also be used by hand from the Serial Monitor.
//
// Episode cycle:
//   h -> HOMING: both arms (leader included, torque on) move slowly to the HOME pose
//     -> READY: both arms are held at home with torque
//   g -> leader torque turns off (HOLD THE LEADER!), follower starts following the leader (FOLLOW)
//   x -> HOLD: follower stops in place          h -> home again
//
// Mapping: follower_goal_i = HOME_F_i + SIGN_i * (leader_i - HOME_L_i)
//   i.e. both arms count as being in the same pose when at home; no separate calibration.
//
// Commands (single letters are handled immediately; a G line ends with a newline):
//   h            : both arms go home (ends in READY)
//   g            : start following (immediately from READY; with a slow sync from WAIT/HOLD)
//   p            : PC control: follower follows G lines (HOLD if none arrives within PC_TIMEOUT_MS)
//   G,a,b,c,d,e  : (in PC mode) follower goals [0..1023]
//   x            : HOLD (follower holds its current position, torque on)
//   q            : torque OFF on BOTH arms, WAIT (the arms collapse, hold them!)
//   r            : toggle the data stream
//   ?            : status
//
// Data line (every loop while the stream is on):
//   D,t_ms,state,l1..l5,g1..g5,f1..f5,y1..y5,volt,tmax,tid
//   state: 0 WAIT, 1 SYNC, 2 FOLLOW, 3 HOLD, 4 PC, 5 HOMING, 6 READY
//   l: leader position  g: follower goal  f: follower position  y: follower load (signed)
//   volt: lowest voltage of the 10 servos [0.1 V]   tmax: temperature of the hottest servo [C], tid: its ID
// On button press:  E,B      Lines starting with '#' are messages for humans.
//
// Button: the user button on the OpenCM always works. For an external button, wire one
//   leg to EXT_BUTTON_PIN and the other to GND.
//
// Safety: tracking error, read error, PC timeout -> HOLD. Voltage < VOLT_MIN_DV or
// temperature > TEMP_MAX_C -> torque off on both arms, sketch stops (the arms collapse!).

#include <DynamixelSDK.h>

#define DEVICENAME        "1"
#define BAUDRATE          1000000
#define PROTOCOL_VERSION  1.0

#define N_JOINT 5
const uint8_t LEADER_IDS[N_JOINT]   = {13, 17, 14,  9, 10};   // bottom to top, 5 = wrist roll
const uint8_t FOLLOWER_IDS[N_JOINT] = {11, 18, 16, 12,  8};
const int8_t  SIGN[N_JOINT]         = {1, 1, -1, -1, 1};

// ---- Home (upright pose) and limits ----------------------------------------------
// 512 = servo middle (150 deg). Roll: 45 degrees = 512 + 45 * 1023 / 300 = 665
const int16_t HOME_L[N_JOINT] = {512, 512, 512, 512, 665};
const int16_t HOME_F[N_JOINT] = {512, 512, 512, 512, 365};

// Follower safe range (0..1023); measure with servo_lab 'watch' and narrow it down
const int16_t FOL_MIN[N_JOINT] = {100, 100, 100, 100, 100};
const int16_t FOL_MAX[N_JOINT] = {923, 923, 923, 923, 923};

// ---- Settings ---------------------------------------------------------------
#define HOME_SPEED       80      // homing speed (~9 rpm). NEVER write 0 (0 = unlimited)
#define HOME_TOL         10      // error at which home counts as reached [units]
#define HOME_TIMEOUT_MS  10000
#define FOLLOWER_SPEED   400
#define SLEW_SYNC_UPS    40      // goal advance rate during sync [units/s]
#define SLEW_RUN_UPS     1200    // goal change limit during teleop
#define SLEW_PC_UPS      300     // goal change limit in PC mode (~88 degrees/s)
#define SYNC_TOL         6
#define SYNC_TIMEOUT_MS  30000
#define PC_TIMEOUT_MS    300

#define LEAD_MS          60      // latency compensation [ms]; 0 = off
#define LEAD_MAX         30
#define VEL_ALPHA        0.3f

#define LOOP_MS          10
#define JUMP_MAX         100     // largest change accepted on the leader in one loop [units]
#define READ_FAIL_MAX    10
#define TRACK_ERR_MAX    80
#define TRACK_ERR_MS     500
#define VOLT_MIN_DV      64      // emergency stop; battery monitoring is done on the PC (record.py)
#define TEMP_MAX_C       60      // emergency stop; the recorder warns/blocks at lower thresholds (50/55 C)
#define TORQUE_STAGGER_MS 50

#define EXT_BUTTON_PIN   16      // external button (pin -> GND); -1 = none
#define BUTTON_DEBOUNCE_MS 40
// --------------------------------------------------------------------------

#define ADDR_TORQUE_ENABLE    24
#define ADDR_GOAL_POSITION    30
#define ADDR_MOVING_SPEED     32
#define ADDR_PRESENT_POSITION 36
#define READ_LEN              8      // position, speed, load, voltage, temperature

dynamixel::PortHandler    *portHandler;
dynamixel::PacketHandler  *packetHandler;
dynamixel::GroupSyncWrite *groupSyncWrite;

enum State { ST_WAIT = 0, ST_SYNC = 1, ST_RUN = 2, ST_HOLD = 3, ST_PC = 4, ST_HOMING = 5, ST_READY = 6 };
const char *STATE_NAME[] = {"WAIT", "SYNC", "FOLLOW", "HOLD", "PC", "HOMING", "READY"};
State state = ST_WAIT;
bool  streamOn = false;

int16_t  lpos[N_JOINT], fpos[N_JOINT], fload[N_JOINT];
uint8_t  lvolt[N_JOINT], fvolt[N_JOINT], ltemp[N_JOINT], ftemp[N_JOINT];
uint8_t  lfail[N_JOINT], ffail[N_JOINT];
float    goalF[N_JOINT], tgtPrev[N_JOINT], velF[N_JOINT], pcTarget[N_JOINT];
int      goalInt[N_JOINT];
uint32_t errSince[N_JOINT];
uint32_t prevT = 0, syncStart = 0, lastPcCmd = 0, homeStart = 0;
float    loopHz = 0;

char    lineBuf[64];
uint8_t lineLen = 0;

// ---------------------------------------------------------------------------
float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

void haltForever() {
  while (1) {
    Serial.println("# STOPPED: error message above. Press RESET.");
    delay(2000);
  }
}

void torqueAll(const uint8_t *ids, uint8_t v) {
  uint8_t err = 0;
  for (int i = 0; i < N_JOINT; i++)
    packetHandler->write1ByteTxRx(portHandler, ids[i], ADDR_TORQUE_ENABLE, v, &err);
}

void torqueOffAndHalt(const char *reason) {
  torqueAll(FOLLOWER_IDS, 0);
  torqueAll(LEADER_IDS, 0);
  Serial.print("# STOPPED, TORQUE OFF: ");
  Serial.println(reason);
  haltForever();
}

void checkVoltTemp(uint8_t v, uint8_t t) {
  if (v < VOLT_MIN_DV) torqueOffAndHalt("voltage too low");
  if (t > TEMP_MAX_C)  torqueOffAndHalt("temperature too high");
}

bool readServo(uint8_t id, int16_t *pos, int16_t *load, uint8_t *volt, uint8_t *temp) {
  uint8_t d[READ_LEN];
  uint8_t err = 0;
  if (packetHandler->readTxRx(portHandler, id, ADDR_PRESENT_POSITION, READ_LEN, d, &err) != COMM_SUCCESS)
    return false;
  *pos = d[0] | (d[1] << 8);
  uint16_t ld = d[4] | (d[5] << 8);
  *load = (ld & 0x400) ? -(int16_t)(ld & 0x3FF) : (int16_t)(ld & 0x3FF);  // bit10 = direction
  *volt = d[6];
  *temp = d[7];
  return true;
}

// Read the leader. filter: reject sudden jumps. Returns the index of a joint with too many consecutive failures, else -1.
int readLeaders(bool filter) {
  int bad = -1;
  for (int i = 0; i < N_JOINT; i++) {
    int16_t p, ld; uint8_t v, t;
    bool ok = readServo(LEADER_IDS[i], &p, &ld, &v, &t);
    if (ok) { checkVoltTemp(v, t); lvolt[i] = v; ltemp[i] = t; }
    if (ok && filter && abs(p - lpos[i]) > JUMP_MAX) ok = false;
    if (ok) { lpos[i] = p; lfail[i] = 0; }
    else {
      if (lfail[i] < 255) lfail[i]++;
      if (lfail[i] > READ_FAIL_MAX) bad = i;
    }
  }
  return bad;
}

int readFollowers() {
  int bad = -1;
  for (int i = 0; i < N_JOINT; i++) {
    int16_t p, ld; uint8_t v, t;
    if (readServo(FOLLOWER_IDS[i], &p, &ld, &v, &t)) {
      checkVoltTemp(v, t);
      fpos[i] = p; fload[i] = ld; fvolt[i] = v; ftemp[i] = t; ffail[i] = 0;
    } else {
      if (ffail[i] < 255) ffail[i]++;
      if (ffail[i] > READ_FAIL_MAX) bad = i;
    }
  }
  return bad;
}

bool readAllRetry() {
  for (int k = 0; k < 3; k++) {
    for (int i = 0; i < N_JOINT; i++) lfail[i] = ffail[i] = 0;
    readLeaders(false);
    readFollowers();
    bool ok = true;
    for (int i = 0; i < N_JOINT; i++) if (lfail[i] || ffail[i]) ok = false;
    if (ok) return true;
  }
  Serial.println("# read failed");
  return false;
}

int minVolt() {
  int v = 255;
  for (int i = 0; i < N_JOINT; i++) { if (lvolt[i] < v) v = lvolt[i]; if (fvolt[i] < v) v = fvolt[i]; }
  return v;
}

// Hottest servo: temperature [C] and its ID
int maxTemp(uint8_t *id) {
  int m = 0;
  for (int i = 0; i < N_JOINT; i++) {
    if (ltemp[i] > m) { m = ltemp[i]; *id = LEADER_IDS[i]; }
    if (ftemp[i] > m) { m = ftemp[i]; *id = FOLLOWER_IDS[i]; }
  }
  return m;
}

float mapTarget(int i) {
  float t = (float)HOME_F[i] + (float)SIGN[i] * ((float)lpos[i] - (float)HOME_L[i]);
  return clampf(t, (float)FOL_MIN[i], (float)FOL_MAX[i]);
}

void writeGoals() {
  groupSyncWrite->clearParam();
  for (int i = 0; i < N_JOINT; i++) {
    uint8_t data[2] = {(uint8_t)(goalInt[i] & 0xFF), (uint8_t)((goalInt[i] >> 8) & 0xFF)};
    groupSyncWrite->addParam(FOLLOWER_IDS[i], data);
  }
  groupSyncWrite->txPacket();
}

void freezeHere(const char *reason) {
  readFollowers();
  for (int i = 0; i < N_JOINT; i++) { goalInt[i] = fpos[i]; goalF[i] = fpos[i]; }
  writeGoals();
  Serial.print("# FROZEN: ");
  Serial.println(reason);
  Serial.println("# follower is holding in place. h = home, g = follow, q = torque off");
  state = ST_HOLD;
}

// ---- Home ------------------------------------------------------------------
void startHome() {
  if (!readAllRetry()) return;
  uint8_t err = 0;
  // first enable torque at the current position (no jump), then set the goal to home; the speed limit moves it slowly
  for (int i = 0; i < N_JOINT; i++) {
    packetHandler->write2ByteTxRx(portHandler, LEADER_IDS[i],   ADDR_MOVING_SPEED, HOME_SPEED, &err);
    packetHandler->write2ByteTxRx(portHandler, FOLLOWER_IDS[i], ADDR_MOVING_SPEED, HOME_SPEED, &err);
    packetHandler->write2ByteTxRx(portHandler, LEADER_IDS[i],   ADDR_GOAL_POSITION, lpos[i], &err);
    packetHandler->write2ByteTxRx(portHandler, FOLLOWER_IDS[i], ADDR_GOAL_POSITION, fpos[i], &err);
  }
  for (int i = 0; i < N_JOINT; i++) {
    packetHandler->write1ByteTxRx(portHandler, LEADER_IDS[i],   ADDR_TORQUE_ENABLE, 1, &err);
    packetHandler->write1ByteTxRx(portHandler, FOLLOWER_IDS[i], ADDR_TORQUE_ENABLE, 1, &err);
    delay(TORQUE_STAGGER_MS);
  }
  for (int i = 0; i < N_JOINT; i++) {
    packetHandler->write2ByteTxRx(portHandler, LEADER_IDS[i],   ADDR_GOAL_POSITION, HOME_L[i], &err);
    packetHandler->write2ByteTxRx(portHandler, FOLLOWER_IDS[i], ADDR_GOAL_POSITION, HOME_F[i], &err);
    goalInt[i] = HOME_F[i];
    goalF[i] = HOME_F[i];
  }
  homeStart = millis();
  state = ST_HOMING;
  Serial.println("# HOMING: both arms are moving slowly to the upright pose");
}

void homingTick(uint32_t now) {
  bool done = true;
  for (int i = 0; i < N_JOINT; i++) {
    if (abs(lpos[i] - HOME_L[i]) > HOME_TOL || abs(fpos[i] - HOME_F[i]) > HOME_TOL) done = false;
  }
  if (done) {
    state = ST_READY;
    Serial.println("# READY: both arms at home. Hold the leader, 'g' (or button)");
    return;
  }
  if (now - homeStart > HOME_TIMEOUT_MS) {
    Serial.print("# could not reach home (leader/follower - home):");
    for (int i = 0; i < N_JOINT; i++) {
      Serial.print(" "); Serial.print(lpos[i] - HOME_L[i]); Serial.print("/"); Serial.print(fpos[i] - HOME_F[i]);
    }
    Serial.println();
    freezeHere("homing timeout (jammed? are the HOME values reachable?)");
  }
}

// ---- Follow / PC ---------------------------------------------------------------
// Enable follower torque at its current position (no jump) and reset the following state
bool armFollower() {
  if (!readAllRetry()) return false;
  for (int i = 0; i < N_JOINT; i++) {
    if (fpos[i] < FOL_MIN[i] || fpos[i] > FOL_MAX[i]) {
      Serial.print("# follower joint "); Serial.print(i + 1);
      Serial.println(" outside the safe range (FOL_MIN/MAX), not started");
      return false;
    }
  }
  uint8_t err = 0;
  for (int i = 0; i < N_JOINT; i++) {
    goalF[i] = fpos[i];
    goalInt[i] = fpos[i];
    tgtPrev[i] = mapTarget(i);
    velF[i] = 0;
    errSince[i] = 0;
    packetHandler->write2ByteTxRx(portHandler, FOLLOWER_IDS[i], ADDR_MOVING_SPEED, FOLLOWER_SPEED, &err);
    packetHandler->write2ByteTxRx(portHandler, FOLLOWER_IDS[i], ADDR_GOAL_POSITION, fpos[i], &err);
  }
  bool wasOn = (state == ST_READY || state == ST_HOLD || state == ST_PC);
  for (int i = 0; i < N_JOINT; i++) {
    packetHandler->write1ByteTxRx(portHandler, FOLLOWER_IDS[i], ADDR_TORQUE_ENABLE, 1, &err);
    if (!wasOn) delay(TORQUE_STAGGER_MS);
  }
  prevT = syncStart = millis();
  return true;
}

void startFollow() {
  torqueAll(LEADER_IDS, 0);   // leader released: the user must be holding it
  if (!armFollower()) return;
  state = ST_SYNC;
  Serial.println("# FOLLOW starting (leader released)");
}

void startPC() {
  if (!armFollower()) return;
  for (int i = 0; i < N_JOINT; i++) pcTarget[i] = fpos[i];
  lastPcCmd = millis();
  state = ST_PC;
  Serial.println("# PC mode: waiting for G,a,b,c,d,e");
}

void printStatus() {
  Serial.print("# state="); Serial.print(STATE_NAME[state]);
  Serial.print(" stream="); Serial.print(streamOn ? 1 : 0);
  Serial.print(" hz="); Serial.print(loopHz, 1);
  uint8_t tid = 0;
  Serial.print(" volt="); Serial.print(minVolt() / 10.0f, 1);
  Serial.print(" temp="); Serial.print(maxTemp(&tid)); Serial.print("C (ID "); Serial.print(tid); Serial.println(")");
}

// ---------------------------------------------------------------------------
void handleLine(char *s) {
  if (s[0] == 'G' && s[1] == ',') {
    if (state != ST_PC) return;   // silently ignore outside PC mode
    float v[N_JOINT];
    char *p = s + 2;
    for (int i = 0; i < N_JOINT; i++) {
      char *end;
      v[i] = strtol(p, &end, 10);
      if (end == p) return;       // missing/corrupt line
      p = (*end == ',') ? end + 1 : end;
    }
    for (int i = 0; i < N_JOINT; i++) pcTarget[i] = clampf(v[i], FOL_MIN[i], FOL_MAX[i]);
    lastPcCmd = millis();
    return;
  }
  switch (s[0]) {
    case 'h': startHome(); break;
    case 'g': if (state == ST_WAIT || state == ST_HOLD || state == ST_READY) startFollow(); break;
    case 'p': if (state == ST_WAIT || state == ST_HOLD || state == ST_READY) startPC(); break;
    case 'x': if (state == ST_SYNC || state == ST_RUN || state == ST_PC || state == ST_HOMING) freezeHere("user (x)"); break;
    case 'q':
      torqueAll(FOLLOWER_IDS, 0);
      torqueAll(LEADER_IDS, 0);
      state = ST_WAIT;
      Serial.println("# torque off on both arms (WAIT)");
      break;
    case 'r': streamOn = !streamOn; Serial.print("# stream "); Serial.println(streamOn ? "ON" : "OFF"); break;
    case '?': printStatus(); break;
    default:  Serial.print("# unknown command: "); Serial.println(s);
  }
}

void pollSerial() {
  while (Serial.available()) {
    int c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (lineLen) { lineBuf[lineLen] = 0; handleLine(lineBuf); lineLen = 0; }
    } else if (lineLen == 0 && c != 'G') {
      if (c > ' ') { char s[2] = {(char)c, 0}; handleLine(s); }   // single-letter command: no need to wait for a newline
    } else if (lineLen < sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = (char)c;
    }
  }
}

void pollButton() {
  static bool stable = false, last = false;
  static uint32_t changed = 0;
  bool raw = digitalRead(BOARD_BUTTON_PIN) == HIGH;            // board button: HIGH when pressed
  if (EXT_BUTTON_PIN >= 0) raw = raw || digitalRead(EXT_BUTTON_PIN) == LOW;   // external: GND when pressed
  uint32_t now = millis();
  if (raw != last) { last = raw; changed = now; }
  if (raw != stable && now - changed > BUTTON_DEBOUNCE_MS) {
    stable = raw;
    if (stable) Serial.println("E,B");
  }
}

void streamRow(uint32_t now) {
  char buf[220];
  int n = snprintf(buf, sizeof(buf), "D,%lu,%d", (unsigned long)now, (int)state);
  for (int i = 0; i < N_JOINT; i++) n += snprintf(buf + n, sizeof(buf) - n, ",%d", lpos[i]);
  for (int i = 0; i < N_JOINT; i++) n += snprintf(buf + n, sizeof(buf) - n, ",%d", goalInt[i]);
  for (int i = 0; i < N_JOINT; i++) n += snprintf(buf + n, sizeof(buf) - n, ",%d", fpos[i]);
  for (int i = 0; i < N_JOINT; i++) n += snprintf(buf + n, sizeof(buf) - n, ",%d", fload[i]);
  uint8_t tid = 0;
  int tmax = maxTemp(&tid);
  n += snprintf(buf + n, sizeof(buf) - n, ",%d,%d,%d\n", minVolt(), tmax, tid);
  Serial.write((const uint8_t *)buf, n);
}

void tick(uint32_t now) {
  float dt = (float)(now - prevT) / 1000.0f;
  if (dt < 0.001f) dt = 0.001f;
  prevT = now;
  loopHz = 0.9f * loopHz + 0.1f * (1.0f / dt);

  bool teleop = (state == ST_SYNC || state == ST_RUN);

  // 1) Leader (with the jump filter during teleop)
  int lbad = readLeaders(teleop);
  if (teleop && lbad >= 0) {
    Serial.print("# leader joint "); Serial.print(lbad + 1); Serial.println(" unreadable or sudden jump (dead zone?)");
    freezeHere("leader read");
  }

  // 2) Goals
  if (state == ST_SYNC || state == ST_RUN) {
    float maxStep = ((state == ST_SYNC) ? (float)SLEW_SYNC_UPS : (float)SLEW_RUN_UPS) * dt;
    bool synced = true;
    for (int i = 0; i < N_JOINT; i++) {
      float base = mapTarget(i);
      float v = (base - tgtPrev[i]) / dt;
      tgtPrev[i] = base;
      velF[i] = (1.0f - VEL_ALPHA) * velF[i] + VEL_ALPHA * v;
      float lead = clampf(velF[i] * (float)LEAD_MS / 1000.0f, -(float)LEAD_MAX, (float)LEAD_MAX);
      float target = clampf(base + lead, (float)FOL_MIN[i], (float)FOL_MAX[i]);
      goalF[i] += clampf(target - goalF[i], -maxStep, maxStep);
      if (fabsf(target - goalF[i]) > (float)SYNC_TOL) synced = false;
      goalInt[i] = (int)(goalF[i] + 0.5f);
    }
    writeGoals();
    if (state == ST_SYNC) {
      if (synced) { state = ST_RUN; Serial.println("# FOLLOW"); }
      else if (now - syncStart > SYNC_TIMEOUT_MS) freezeHere("sync timeout");
    }
  } else if (state == ST_PC) {
    if (now - lastPcCmd > PC_TIMEOUT_MS) {
      freezeHere("no commands from the PC");
    } else {
      float maxStep = (float)SLEW_PC_UPS * dt;
      for (int i = 0; i < N_JOINT; i++) {
        goalF[i] += clampf(pcTarget[i] - goalF[i], -maxStep, maxStep);
        goalInt[i] = (int)(goalF[i] + 0.5f);
      }
      writeGoals();
    }
  } else if (state == ST_WAIT) {
    for (int i = 0; i < N_JOINT; i++) goalInt[i] = (int)mapTarget(i);   // information only
  }

  // 3) Follower + tracking error
  int fbad = readFollowers();
  bool active = (state == ST_SYNC || state == ST_RUN || state == ST_PC);
  if ((active || state == ST_HOMING) && fbad >= 0) freezeHere("follower unreadable");
  if (active) {
    for (int j = 0; j < N_JOINT; j++) {
      if (abs(goalInt[j] - fpos[j]) > TRACK_ERR_MAX) {
        if (errSince[j] == 0) errSince[j] = now;
        else if (now - errSince[j] > TRACK_ERR_MS) {
          Serial.print("# large tracking error, joint "); Serial.println(j + 1);
          freezeHere("tracking error (collision/jam/too fast?)");
          break;
        }
      } else {
        errSince[j] = 0;
      }
    }
  }
  if (state == ST_HOMING) homingTick(now);

  // 4) Output
  if (streamOn) {
    streamRow(now);
  } else {
    static uint32_t nextPrint = 0;
    if (now >= nextPrint) {
      nextPrint = now + 500;
      Serial.print("# "); Serial.print(STATE_NAME[state]);
      Serial.print(" hz="); Serial.print(loopHz, 0);
      uint8_t tid = 0;
      Serial.print(" volt="); Serial.print(minVolt() / 10.0f, 1);
      Serial.print(" temp="); Serial.print(maxTemp(&tid)); Serial.print("C");
      Serial.print("  L:"); for (int i = 0; i < N_JOINT; i++) { Serial.print(" "); Serial.print(lpos[i]); }
      Serial.print("  F:"); for (int i = 0; i < N_JOINT; i++) { Serial.print(" "); Serial.print(fpos[i]); }
      Serial.print("  goal:"); for (int i = 0; i < N_JOINT; i++) { Serial.print(" "); Serial.print(goalInt[i]); }
      Serial.println();
    }
  }
}

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 5000);
  Serial.println("# arm_rec started");
  pinMode(BOARD_BUTTON_PIN, INPUT_PULLDOWN);
  if (EXT_BUTTON_PIN >= 0) pinMode(EXT_BUTTON_PIN, INPUT_PULLUP);

  portHandler    = dynamixel::PortHandler::getPortHandler(DEVICENAME);
  packetHandler  = dynamixel::PacketHandler::getPacketHandler(PROTOCOL_VERSION);
  groupSyncWrite = new dynamixel::GroupSyncWrite(portHandler, packetHandler, ADDR_GOAL_POSITION, 2);

  if (!portHandler->openPort())            { Serial.println("# ERROR: could not open port"); haltForever(); }
  if (!portHandler->setBaudRate(BAUDRATE)) { Serial.println("# ERROR: could not set baud rate"); haltForever(); }

  uint8_t err = 0;
  bool allOk = true;
  for (int i = 0; i < N_JOINT; i++) {
    uint16_t model = 0;
    if (packetHandler->ping(portHandler, LEADER_IDS[i], &model, &err) != COMM_SUCCESS) {
      Serial.print("# ERROR: leader did not respond, ID "); Serial.println(LEADER_IDS[i]); allOk = false;
    }
    if (packetHandler->ping(portHandler, FOLLOWER_IDS[i], &model, &err) != COMM_SUCCESS) {
      Serial.print("# ERROR: follower did not respond, ID "); Serial.println(FOLLOWER_IDS[i]); allOk = false;
    }
  }
  if (!allOk) { Serial.println("# servo(s) missing (cable, power/battery, ID?), stopped"); haltForever(); }

  torqueAll(LEADER_IDS, 0);
  torqueAll(FOLLOWER_IDS, 0);
  readAllRetry();
  Serial.println("# ready: 10 servos, torque off. h = home, '?' = status");
  prevT = millis();
}

void loop() {
  static uint32_t next = 0;
  pollSerial();
  pollButton();
  uint32_t now = millis();
  if (now < next) return;
  next = now + LOOP_MS;
  tick(now);
}
