// servo_lab.ino - AX-12A servo inspection console (OpenCM9.04)
//
// Nothing happens on its own; only the command you type runs and its result is printed.
// Serial Monitor: 115200, line ending "Newline". 'help' = command list.
// Long-running commands (watch, pair, follow, go, sweep, home) stop on any letter + Enter (e.g. s).
//
// Position unit: 0..1023 = 0..300 degrees (1 unit ~0.29 deg), 512 = middle (150 deg).
// DEAD ZONE: between 300..360 degrees (between 1023 and 0). Position can't be read or controlled there.
//
// Settings (home, range) are stored in the OpenCM's EEPROM and survive reset/power loss.

#include <DynamixelSDK.h>
#include <EEPROM.h>

#define DEVICENAME        "1"
#define BAUDRATE          1000000
#define PROTOCOL_VERSION  1.0

#define N_JOINT 5
#define N_SERVO (2 * N_JOINT)
const uint8_t LEADER_IDS[N_JOINT]   = {13, 17, 14,  9, 10};   // bottom to top
const uint8_t FOLLOWER_IDS[N_JOINT] = {11, 18, 16, 12,  8};
const int8_t  SIGN[N_JOINT]         = {1, 1, -1, -1, 1};      // default for follow

#define DEFAULT_SPEED  100   // ~11 rpm, slow and safe (0 = UNLIMITED, never used)
#define FOLLOW_SPEED   300
#define LOAD_STOP      700   // motion stops if |load| exceeds this for LOAD_STOP_MS (0..1023)
#define LOAD_STOP_MS   300
#define JUMP           100   // change larger than this between two reads = jump (dead zone?)
#define EDGE           30    // this close to 0 or 1023 = edge of the dead zone

// AX-12A register addresses (Protocol 1.0)
#define A_MODEL        0
#define A_FW           2
#define A_RET_DELAY    5
#define A_CW_LIMIT     6
#define A_CCW_LIMIT    8
#define A_TEMP_LIMIT   11
#define A_MIN_VOLT     12
#define A_MAX_VOLT     13
#define A_MAX_TORQUE   14
#define A_STATUS_RET   16
#define A_ALARM_SHUT   18
#define A_TORQUE       24
#define A_LED          25
#define A_CW_MARGIN    26
#define A_CCW_MARGIN   27
#define A_CW_SLOPE     28
#define A_CCW_SLOPE    29
#define A_GOAL         30
#define A_SPEED        32
#define A_TORQUE_LIMIT 34
#define A_POS          36
#define A_LOAD         40
#define A_MOVING       46
#define A_PUNCH        48

#define CFG_MAGIC 0xA12C
#define MAX_TOK   12

// (all structs at the top because of Arduino's automatic prototypes)
struct St    { int16_t pos, load; uint8_t volt, temp; };
struct Mon   { uint8_t id; int start, cur, prev, lo, hi, jumps; bool ok; };
struct Cfg   { uint16_t magic; int16_t home[N_SERVO], lo[N_SERVO], hi[N_SERVO]; };

dynamixel::PortHandler   *ph;
dynamixel::PacketHandler *pk;
uint8_t lastErr = 0;
int     lastComm = COMM_SUCCESS;   // result of the last transaction (noResp prints the reason)
char    line[96];
uint8_t lineLen = 0;
Cfg     cfg;
bool    cfgPersist = true;

// ---------------------------------------------------------------------------
float deg(int p) { return p * 300.0f / 1023.0f; }
int clampi(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }
int imax(int a, int b) { return a > b ? a : b; }
int imin(int a, int b) { return a < b ? a : b; }

// Table order: 0..4 leader joints 1..5, 5..9 follower joints 1..5. -1 if not in the table.
int slotOf(uint8_t id) {
  for (int j = 0; j < N_JOINT; j++) {
    if (LEADER_IDS[j] == id) return j;
    if (FOLLOWER_IDS[j] == id) return N_JOINT + j;
  }
  return -1;
}
uint8_t idOf(int k) { return k < N_JOINT ? LEADER_IDS[k] : FOLLOWER_IDS[k - N_JOINT]; }

// Like "L4(9)": leader/follower, joint number, ID
void printLabel(uint8_t id) {
  int k = slotOf(id);
  if (k < 0) { Serial.print("ID"); Serial.print(id); return; }
  Serial.print(k < N_JOINT ? "L" : "F"); Serial.print(k % N_JOINT + 1);
  Serial.print("("); Serial.print(id); Serial.print(")");
}

// ---- Settings (EEPROM) -------------------------------------------------------
void cfgDefaults() {
  cfg.magic = CFG_MAGIC;
  for (int k = 0; k < N_SERVO; k++) { cfg.home[k] = -1; cfg.lo[k] = 0; cfg.hi[k] = 1023; }
}
void cfgLoad() {
  if (EEPROM.length() < sizeof(Cfg)) {
    cfgPersist = false;
    cfgDefaults();
    Serial.println("# ! EEPROM too small, settings can't be stored permanently");
    return;
  }
  EEPROM.get(0, cfg);
  if (cfg.magic != CFG_MAGIC) { cfgDefaults(); Serial.println("# no saved settings, using defaults"); }
  else Serial.println("# settings loaded from EEPROM (see 'cfg')");
}
void cfgSave() {
  if (cfgPersist) EEPROM.put(0, cfg);
}

// ---- Bus -------------------------------------------------------------------
bool rd1(uint8_t id, uint8_t a, uint8_t *v) {
  uint8_t e = 0; lastComm = pk->read1ByteTxRx(ph, id, a, v, &e); lastErr = e; return lastComm == COMM_SUCCESS;
}
bool rd2(uint8_t id, uint8_t a, uint16_t *v) {
  uint8_t e = 0; lastComm = pk->read2ByteTxRx(ph, id, a, v, &e); lastErr = e; return lastComm == COMM_SUCCESS;
}
bool wr1(uint8_t id, uint8_t a, uint8_t v) {
  uint8_t e = 0; lastComm = pk->write1ByteTxRx(ph, id, a, v, &e); lastErr = e; return lastComm == COMM_SUCCESS;
}
bool wr2(uint8_t id, uint8_t a, uint16_t v) {
  uint8_t e = 0; lastComm = pk->write2ByteTxRx(ph, id, a, v, &e); lastErr = e; return lastComm == COMM_SUCCESS;
}

bool readSt(uint8_t id, St &s) {
  uint8_t d[8]; uint8_t e = 0;
  lastComm = pk->readTxRx(ph, id, A_POS, 8, d, &e);
  if (lastComm != COMM_SUCCESS) return false;
  lastErr = e;
  s.pos = d[0] | (d[1] << 8);
  uint16_t ld = d[4] | (d[5] << 8);
  s.load = (ld & 0x400) ? -(int16_t)(ld & 0x3FF) : (int16_t)(ld & 0x3FF);   // bit10 = direction
  s.volt = d[6];
  s.temp = d[7];
  return true;
}

void printErr(uint8_t e) {
  if (!e) return;
  Serial.print("  [ERROR:");
  if (e & 0x01) Serial.print(" voltage");
  if (e & 0x02) Serial.print(" angle_limit(goal outside CW/CCW)");
  if (e & 0x04) Serial.print(" overheating");
  if (e & 0x08) Serial.print(" range");
  if (e & 0x10) Serial.print(" checksum");
  if (e & 0x20) Serial.print(" OVERLOAD");
  if (e & 0x40) Serial.print(" instruction");
  Serial.print(" ]");
}

void printSt(const St &s) {
  Serial.print("pos="); Serial.print(s.pos);
  Serial.print(" ("); Serial.print(deg(s.pos), 1); Serial.print(" deg)");
  Serial.print(" load="); Serial.print(s.load);
  if (s.pos < EDGE || s.pos > 1023 - EDGE) Serial.print("  <AT DEAD ZONE EDGE>");
  printErr(lastErr);
}

void noResp(uint8_t id) {
  Serial.print("# ID "); Serial.print(id); Serial.print(" not responding: ");
  Serial.print(pk->getTxRxResult(lastComm));
  if (lastComm == COMM_RX_TIMEOUT) Serial.println("  (no reply at all: cable/connector, ID, power?)");
  else if (lastComm == COMM_RX_CORRUPT) Serial.println("  (corrupt reply: two servos with the same ID, loose cable, return delay?)");
  else Serial.println();
}

// true if any letter arrived (line endings don't count)
bool keyHit() {
  bool hit = false;
  while (Serial.available()) {
    int c = Serial.read();
    if (c > ' ') hit = true;
  }
  return hit;
}

void hold(uint8_t id) {
  St s;
  if (readSt(id, s)) wr2(id, A_GOAL, s.pos);
}

// Clips the goal first to the servo's CW/CCW limits, then to the software limit (range). -1 in wheel mode.
int clampToLimits(uint8_t id, int goal) {
  uint16_t cw = 0, ccw = 1023;
  if (!rd2(id, A_CW_LIMIT, &cw) || !rd2(id, A_CCW_LIMIT, &ccw)) { noResp(id); return -1; }
  if (cw == 0 && ccw == 0) { Serial.println("# servo in WHEEL MODE (CW=CCW=0), no position control. 'limit id 0 1023'"); return -1; }
  int lo = cw, hi = ccw;
  int k = slotOf(id);
  if (k >= 0) { lo = imax(cw, cfg.lo[k]); hi = imin(ccw, cfg.hi[k]); }
  int g = clampi(goal, lo, hi);
  if (g != goal) { Serial.print("# goal clipped to limit: "); Serial.print(goal); Serial.print(" -> "); Serial.println(g); }
  return g;
}

// ---- Motion -----------------------------------------------------------------
// Go to the goal, printing along the way. 0 = done, 1 = stopped by user, 2 = jammed/overload, 3 = read error
int moveTo(uint8_t id, int goal, int speed) {
  wr2(id, A_SPEED, speed);
  wr2(id, A_GOAL, goal);
  wr1(id, A_TORQUE, 1);
  uint32_t t0 = millis(), hiSince = 0, nextP = 0;
  St s;
  while (1) {
    if (keyHit()) { hold(id); Serial.println("# stopped, holding in place"); return 1; }
    if (!readSt(id, s)) { noResp(id); return 3; }
    uint32_t now = millis();
    if (now >= nextP) {
      nextP = now + 100;
      Serial.print("  "); printSt(s); Serial.print("  goal="); Serial.println(goal);
    }
    if (lastErr & 0x20) { Serial.println("# OVERLOAD alarm: the servo cut its torque. Check with 'info'"); return 2; }
    if (abs(s.load) > LOAD_STOP) {
      if (!hiSince) hiSince = now;
      else if (now - hiSince > LOAD_STOP_MS) {
        hold(id);
        Serial.print("# JAMMED/COLLIDED: position "); Serial.print(s.pos); Serial.println(", holding in place");
        return 2;
      }
    } else {
      hiSince = 0;
    }
    uint8_t mv = 1;
    rd1(id, A_MOVING, &mv);
    if (!mv && now - t0 > 200) {
      readSt(id, s);
      Serial.print("# done: "); printSt(s);
      Serial.print("  error from goal="); Serial.println(goal - s.pos);
      return 0;
    }
  }
}

// Moves several servos to their goals at the same time
void moveMany(const uint8_t *ids, const int *goals, int n, int speed) {
  bool active[N_SERVO];
  uint32_t hiSince[N_SERVO];
  for (int k = 0; k < n; k++) {
    hold(ids[k]);
    wr2(ids[k], A_SPEED, speed);
    wr2(ids[k], A_GOAL, goals[k]);
    wr1(ids[k], A_TORQUE, 1);
    active[k] = true;
    hiSince[k] = 0;
  }
  uint32_t t0 = millis(), nextP = 0;
  while (1) {
    if (keyHit()) {
      for (int k = 0; k < n; k++) hold(ids[k]);
      Serial.println("# stopped, holding in place");
      return;
    }
    uint32_t now = millis();
    bool print = now >= nextP, any = false;
    if (print) nextP = now + 150;
    for (int k = 0; k < n; k++) {
      St s;
      if (!readSt(ids[k], s)) { if (print) { Serial.print("  "); printLabel(ids[k]); Serial.print("=--"); } continue; }
      if (print) { Serial.print("  "); printLabel(ids[k]); Serial.print("="); Serial.print(s.pos); Serial.print("->"); Serial.print(goals[k]); }
      if (!active[k]) continue;
      if (abs(s.load) > LOAD_STOP) {
        if (!hiSince[k]) hiSince[k] = now;
        else if (now - hiSince[k] > LOAD_STOP_MS) {
          hold(ids[k]);
          active[k] = false;
          Serial.print("  !JAMMED");
          continue;
        }
      } else {
        hiSince[k] = 0;
      }
      uint8_t mv = 1;
      rd1(ids[k], A_MOVING, &mv);
      if (!mv && now - t0 > 200) active[k] = false;
      else any = true;
    }
    if (print) Serial.println();
    if (!any) break;
  }
  Serial.println("# done, torque on (holding in place). To release: t <id..> 0");
}

// ---- Monitoring ------------------------------------------------------------------
void monInit(Mon &m, uint8_t id) {
  St s;
  m.id = id;
  m.ok = readSt(id, s);
  m.start = m.cur = m.prev = m.lo = m.hi = m.ok ? s.pos : 0;
  m.jumps = 0;
}

void monStep(Mon &m) {
  St s;
  m.ok = readSt(m.id, s);
  if (!m.ok) { Serial.print("--"); return; }
  bool jumped = abs(s.pos - m.prev) > JUMP;
  if (jumped) m.jumps++;
  m.prev = m.cur = s.pos;
  if (s.pos < m.lo) m.lo = s.pos;
  if (s.pos > m.hi) m.hi = s.pos;
  Serial.print(m.cur);
  Serial.print("("); if (m.cur - m.start >= 0) Serial.print("+"); Serial.print(m.cur - m.start); Serial.print(")");
  int k = slotOf(m.id);
  if (k >= 0 && (s.pos < cfg.lo[k] || s.pos > cfg.hi[k])) Serial.print("!LIMIT");
  if (s.pos < EDGE || s.pos > 1023 - EDGE) Serial.print("!DEAD");
  if (jumped) Serial.print("!JUMP");
}

void monSummary(const Mon &m) {
  printLabel(m.id); Serial.print(": "); Serial.print(m.lo); Serial.print(".."); Serial.print(m.hi);
  Serial.print(" ("); Serial.print(deg(m.lo), 0); Serial.print(".."); Serial.print(deg(m.hi), 0);
  Serial.print(" deg) jumps="); Serial.print(m.jumps);
}

// Watch servos side by side; does not touch torque. pairMode: ids are (leader, follower) pairs, SIGN suggestion in the summary.
void watchIds(const uint8_t *ids, int n, bool pairMode) {
  Mon m[N_SERVO];
  Serial.print("# watching:");
  for (int k = 0; k < n; k++) {
    monInit(m[k], ids[k]);
    Serial.print("  "); printLabel(ids[k]);
    if (!m[k].ok) { Serial.print("(NO REPLY)"); continue; }
    uint8_t tq = 0;
    rd1(ids[k], A_TORQUE, &tq);
    if (tq) Serial.print("(TORQUE ON)");
  }
  Serial.println();
  Serial.println("# format: position(change since start). A servo with torque on won't turn by hand: 't all 0'. Stop: s+Enter");
  while (!keyHit()) {
    for (int k = 0; k < n; k++) {
      Serial.print(k ? "  " : " ");
      if (pairMode && k % 2 == 0 && k) Serial.print("| ");
      printLabel(ids[k]); Serial.print("=");
      monStep(m[k]);
    }
    Serial.println();
    delay(n > 4 ? 150 : 100);
  }
  Serial.println("# ---- summary ----");
  for (int k = 0; k < n; k++) {
    Serial.print("# "); monSummary(m[k]);
    Serial.print("  last change="); Serial.println(m[k].cur - m[k].start);
  }
  if (!pairMode) return;
  for (int k = 0; k + 1 < n; k += 2) {
    int dl = m[k].cur - m[k].start, df = m[k + 1].cur - m[k + 1].start;
    Serial.print("# joint "); Serial.print(slotOf(ids[k]) % N_JOINT + 1); Serial.print(": ");
    if (abs(dl) < 30 || abs(df) < 30) { Serial.println("too little motion, direction unknown"); continue; }
    Serial.println(((dl > 0) == (df > 0)) ? "same direction, SIGN=1" : "opposite direction, SIGN=-1");
  }
  Serial.println("# (SIGN is only valid if you turned both in the same physical direction)");
}

// ---- Commands ------------------------------------------------------------------
void cmdHelp() {
  Serial.println("# ---- monitoring (does not touch torque) ----");
  Serial.println("# scan                      find the servos on the bus (ID 0..60)");
  Serial.println("# ids                       joint table: leader/follower IDs and current positions");
  Serial.println("# info <id>                 all settings + status + warnings");
  Serial.println("# watch [id id ..]          watch servos side by side live (e.g. watch 13 11). Empty = all");
  Serial.println("# pair [joint ..]           watch the leader+follower pairs of joints (e.g. pair 4, pair 2 4)");
  Serial.println("# led <id>                  LED on for 3 s: which servo is this ID?");
  Serial.println("# comm <id> [n]             communication test: n reads (default 200), how many succeed");
  Serial.println("# ---- motion ----");
  Serial.println("# t <id..|all> <0|1>        torque off/on (e.g. t 13 11 0, t all 0)");
  Serial.println("# go <id> <pos> [speed]     go to a position (0..1023), prints the path");
  Serial.println("# sweep <id> <a> <b> [spd]  a -> b -> a slowly, prints position/load");
  Serial.println("# home <id..|all> [h<spd>]  go to the default position, all at once (e.g. home all, home 11 18 h200)");
  Serial.println("# follow <joint> [1|-1]     only this joint's follower follows the leader");
  Serial.println("# ---- settings (persistent in the OpenCM EEPROM) ----");
  Serial.println("# cfg                       home/range settings and current position of all servos");
  Serial.println("# sethome <id> [pos]        default position; without pos the CURRENT position");
  Serial.println("# range <id> lo|hi          software limit (go/sweep/home/follow won't exceed it): current pos as low/high");
  Serial.println("# range <id> <lo> <hi>      software limit by hand;  range <id> off = no limit");
  Serial.println("# ---- servo EEPROM (careful) ----");
  Serial.println("# limit <id> <cw> <ccw>     the servo's own angle limits; the servo won't go beyond them");
  Serial.println("# tlimit <id> <0..1023>     torque limit (may stay 0 after an overload)");
  Serial.println("# to stop: any letter + Enter (e.g. s)");
}

void cmdScan() {
  Serial.println("# scanning (0..60)...");
  int n = 0;
  for (int id = 0; id <= 60; id++) {
    uint16_t model = 0; uint8_t e = 0;
    if (pk->ping(ph, id, &model, &e) != COMM_SUCCESS) continue;
    uint8_t fw = 0; rd1(id, A_FW, &fw);
    St s; readSt(id, s);
    Serial.print("#  "); printLabel(id);
    Serial.print("  model="); Serial.print(model); Serial.print(model == 12 ? " (AX-12A)" : "");
    Serial.print("  fw="); Serial.print(fw);
    Serial.print("  "); printSt(s); Serial.println();
    n++;
  }
  Serial.print("# "); Serial.print(n); Serial.println(" servos found");
}

void cmdIds() {
  Serial.println("# joint | leader ID: pos (deg) | follower ID: pos (deg)");
  for (int i = 0; i < N_JOINT; i++) {
    St l, f;
    bool lo = readSt(LEADER_IDS[i], l), fo = readSt(FOLLOWER_IDS[i], f);
    Serial.print("#  "); Serial.print(i + 1);
    Serial.print("   | L "); Serial.print(LEADER_IDS[i]); Serial.print(": ");
    if (lo) { Serial.print(l.pos); Serial.print(" ("); Serial.print(deg(l.pos), 1); Serial.print(")"); } else Serial.print("NONE");
    Serial.print("   | F "); Serial.print(FOLLOWER_IDS[i]); Serial.print(": ");
    if (fo) { Serial.print(f.pos); Serial.print(" ("); Serial.print(deg(f.pos), 1); Serial.print(")"); } else Serial.print("NONE");
    Serial.println();
  }
}

void cmdCfg() {
  Serial.println(cfgPersist ? "# settings in EEPROM" : "# no EEPROM, settings only in RAM");
  Serial.println("# servo     home   range(lo..hi)   now  torque");
  for (int k = 0; k < N_SERVO; k++) {
    uint8_t id = idOf(k);
    St s; uint8_t tq = 0;
    bool ok = readSt(id, s);
    rd1(id, A_TORQUE, &tq);
    Serial.print("#  "); printLabel(id); Serial.print("   ");
    if (cfg.home[k] < 0) Serial.print("  -  "); else Serial.print(cfg.home[k]);
    Serial.print("   ");
    if (cfg.lo[k] <= 0 && cfg.hi[k] >= 1023) Serial.print("no limit");
    else { Serial.print(cfg.lo[k]); Serial.print(".."); Serial.print(cfg.hi[k]); }
    Serial.print("   ");
    if (ok) Serial.print(s.pos); else Serial.print("--");
    Serial.print("   "); Serial.println(tq);
  }
}

void cmdInfo(uint8_t id) {
  uint16_t model, cw, ccw, maxT, goal, speed, tlim, punch;
  uint8_t fw, rd, tl, vmin, vmax, ash, tq, cwm, ccwm, cws, ccws, srl = 2;
  St s;
  if (!rd2(id, A_MODEL, &model)) { noResp(id); return; }
  rd1(id, A_FW, &fw); rd1(id, A_RET_DELAY, &rd); rd1(id, A_STATUS_RET, &srl);
  rd2(id, A_CW_LIMIT, &cw); rd2(id, A_CCW_LIMIT, &ccw);
  rd1(id, A_TEMP_LIMIT, &tl); rd1(id, A_MIN_VOLT, &vmin); rd1(id, A_MAX_VOLT, &vmax);
  rd2(id, A_MAX_TORQUE, &maxT); rd1(id, A_ALARM_SHUT, &ash);
  rd1(id, A_TORQUE, &tq); rd1(id, A_CW_MARGIN, &cwm); rd1(id, A_CCW_MARGIN, &ccwm);
  rd1(id, A_CW_SLOPE, &cws); rd1(id, A_CCW_SLOPE, &ccws);
  rd2(id, A_GOAL, &goal); rd2(id, A_SPEED, &speed); rd2(id, A_TORQUE_LIMIT, &tlim); rd2(id, A_PUNCH, &punch);
  readSt(id, s);

  Serial.print("# ---- "); printLabel(id); Serial.println(" ----");
  Serial.print("# model="); Serial.print(model); Serial.print("  fw="); Serial.print(fw);
  Serial.print("  return_delay="); Serial.print(rd); Serial.print(" ("); Serial.print(rd * 2); Serial.print(" us)  status_return_level="); Serial.println(srl);
  Serial.print("# servo angle limits: CW="); Serial.print(cw); Serial.print(" ("); Serial.print(deg(cw), 1);
  Serial.print(" deg)  CCW="); Serial.print(ccw); Serial.print(" ("); Serial.print(deg(ccw), 1); Serial.println(" deg)");
  int k = slotOf(id);
  if (k >= 0) {
    Serial.print("# sketch settings: home="); Serial.print(cfg.home[k]);
    Serial.print("  range="); Serial.print(cfg.lo[k]); Serial.print(".."); Serial.println(cfg.hi[k]);
  }
  Serial.print("# torque: on="); Serial.print(tq); Serial.print("  limit(RAM)="); Serial.print(tlim);
  Serial.print("  max(EEPROM)="); Serial.println(maxT);
  Serial.print("# goal="); Serial.print(goal); Serial.print("  speed="); Serial.print(speed);
  Serial.print("  compliance margin="); Serial.print(cwm); Serial.print("/"); Serial.print(ccwm);
  Serial.print(" slope="); Serial.print(cws); Serial.print("/"); Serial.print(ccws);
  Serial.print("  punch="); Serial.println(punch);
  Serial.print("# voltage="); Serial.print(s.volt / 10.0f, 1); Serial.print(" V (allowed "); Serial.print(vmin / 10.0f, 1);
  Serial.print(".."); Serial.print(vmax / 10.0f, 1); Serial.print(")  temperature="); Serial.print(s.temp);
  Serial.print(" C (limit "); Serial.print(tl); Serial.print(")  alarm_shutdown=0x"); Serial.println(ash, HEX);
  Serial.print("# now: "); printSt(s); Serial.println();

  if (cw == 0 && ccw == 0) Serial.println("# ! WHEEL MODE: no position control");
  if (srl != 2) Serial.println("# ! status_return_level is not 2: write commands get no reply, you will see 'not responding'");
  if (rd < 5) Serial.println("# ! return delay too small: the OpenCM may miss the reply (corrupt/missing reply)");
  if (tlim == 0) Serial.println("# ! torque limit is 0: the servo will NOT move (after an overload). 'tlimit <id> 1023'");
  if (maxT < 300) Serial.println("# ! max torque (EEPROM) is low: the servo will be weak");
  if (speed == 0 && tq) Serial.println("# ! speed=0 = UNLIMITED speed");
  if (s.pos < EDGE || s.pos > 1023 - EDGE) Serial.println("# ! position at the edge of the dead zone");
}

// Communication test: reads n times and counts the results
void cmdComm(uint8_t id, int n) {
  int ok = 0, tmo = 0, cor = 0, other = 0;
  uint8_t errs = 0;
  uint32_t t0 = micros();
  for (int i = 0; i < n; i++) {
    St s;
    if (readSt(id, s)) { ok++; errs |= lastErr; }
    else if (lastComm == COMM_RX_TIMEOUT) tmo++;
    else if (lastComm == COMM_RX_CORRUPT) cor++;
    else other++;
  }
  uint32_t us = (micros() - t0) / n;
  Serial.print("# "); printLabel(id); Serial.print(" "); Serial.print(n); Serial.print(" reads: ok=");
  Serial.print(ok); Serial.print(" no_reply=");
  Serial.print(tmo); Serial.print(" corrupt="); Serial.print(cor); Serial.print(" other="); Serial.print(other);
  Serial.print("  ("); Serial.print(us); Serial.print(" us per read)");
  printErr(errs);
  Serial.println();
  if (ok == n) Serial.println("# communication is solid");
  else if (ok == 0 && tmo == n) Serial.println("# no reply at all: wrong ID, servo unpowered or cable/connector disconnected");
  else Serial.println("# INTERMITTENT errors: loose connector, damaged cable or two servos with the same ID");
}

void cmdLed(uint8_t id) {
  if (!wr1(id, A_LED, 1)) { noResp(id); return; }
  Serial.print("# "); printLabel(id); Serial.println(" LED on (3 s)");
  delay(3000);
  wr1(id, A_LED, 0);
}

void cmdTorque(const uint8_t *ids, int n, int v) {
  for (int k = 0; k < n; k++) {
    if (v) hold(ids[k]);   // when enabling, stay at the current position instead of jumping to an old goal
    if (!wr1(ids[k], A_TORQUE, v)) { noResp(ids[k]); continue; }
    Serial.print("# "); printLabel(ids[k]); Serial.print(" torque="); Serial.println(v);
  }
  if (!v) Serial.println("# careful: an arm with torque off collapses under its own weight");
}

void cmdGo(uint8_t id, int goal, int speed) {
  goal = clampToLimits(id, goal);
  if (goal < 0) return;
  hold(id);
  Serial.print("# "); printLabel(id); Serial.print(" -> "); Serial.print(goal);
  Serial.print(" ("); Serial.print(deg(goal), 1); Serial.print(" deg), speed "); Serial.println(speed);
  moveTo(id, goal, speed);
  Serial.println("# torque left on (holds in place). To release: t <id> 0");
}

void cmdSweep(uint8_t id, int a, int b, int speed) {
  a = clampToLimits(id, a);
  b = clampToLimits(id, b);
  if (a < 0 || b < 0) return;
  hold(id);
  Serial.print("# sweep "); printLabel(id); Serial.print(": "); Serial.print(a); Serial.print(" -> ");
  Serial.print(b); Serial.print(" -> "); Serial.println(a);
  if (moveTo(id, a, speed)) return;
  Serial.println("# --- a to b ---");
  if (moveTo(id, b, speed)) return;
  Serial.println("# --- b to a ---");
  moveTo(id, a, speed);
  Serial.println("# torque left on. To release: t <id> 0");
}

void cmdHome(const uint8_t *ids, int n, int speed) {
  uint8_t mids[N_SERVO];
  int goals[N_SERVO], m = 0;
  for (int k = 0; k < n; k++) {
    int s = slotOf(ids[k]);
    if (s < 0 || cfg.home[s] < 0) {
      Serial.print("# "); printLabel(ids[k]); Serial.println(" has no home (sethome), skipped");
      continue;
    }
    int g = clampToLimits(ids[k], cfg.home[s]);
    if (g < 0) continue;
    mids[m] = ids[k];
    goals[m++] = g;
  }
  if (!m) return;
  Serial.print("# going home, speed "); Serial.println(speed);
  moveMany(mids, goals, m, speed);
}

void cmdSetHome(uint8_t id, int pos) {
  int k = slotOf(id);
  if (k < 0) { Serial.println("# this ID is not in the joint table (LEADER_IDS/FOLLOWER_IDS)"); return; }
  if (pos < 0) {
    St s;
    if (!readSt(id, s)) { noResp(id); return; }
    pos = s.pos;
  }
  if (pos > 1023) { Serial.println("# 0..1023"); return; }
  cfg.home[k] = pos;
  cfgSave();
  Serial.print("# "); printLabel(id); Serial.print(" home = "); Serial.print(pos);
  Serial.print(" ("); Serial.print(deg(pos), 1); Serial.println(" deg)");
}

void cmdRange(uint8_t id, char *a, char *b) {
  int k = slotOf(id);
  if (k < 0) { Serial.println("# this ID is not in the joint table (LEADER_IDS/FOLLOWER_IDS)"); return; }
  int lo = cfg.lo[k], hi = cfg.hi[k];
  if (!strcmp(a, "off")) { lo = 0; hi = 1023; }
  else if (!strcmp(a, "lo") || !strcmp(a, "hi")) {
    St s;
    if (!readSt(id, s)) { noResp(id); return; }
    if (a[0] == 'l') lo = s.pos; else hi = s.pos;
  } else if (b) { lo = atoi(a); hi = atoi(b); }
  else { Serial.println("# range <id> lo|hi|off  or  range <id> <lo> <hi>"); return; }
  if (lo < 0 || hi > 1023 || lo >= hi) {
    Serial.print("# invalid: lo="); Serial.print(lo); Serial.print(" hi="); Serial.print(hi);
    Serial.println(" (must be 0 <= lo < hi; the smaller number is lo, the larger hi. Direction may be reversed)");
    return;
  }
  cfg.lo[k] = lo;
  cfg.hi[k] = hi;
  cfgSave();
  Serial.print("# "); printLabel(id); Serial.print(" range = "); Serial.print(lo); Serial.print(".."); Serial.print(hi);
  Serial.print(" ("); Serial.print(deg(lo), 0); Serial.print(".."); Serial.print(deg(hi), 0); Serial.println(" deg)");
  if (lo < EDGE || hi > 1023 - EDGE) Serial.println("# ! limit too close to the dead zone");
}

void cmdLimit(uint8_t id, int cw, int ccw) {
  if (cw < 0 || ccw > 1023 || cw >= ccw) { Serial.println("# invalid: must be 0 <= cw < ccw <= 1023"); return; }
  if (!wr2(id, A_CW_LIMIT, cw)) { noResp(id); return; }
  delay(10);
  wr2(id, A_CCW_LIMIT, ccw);
  delay(10);
  uint16_t a = 0, b = 0;
  rd2(id, A_CW_LIMIT, &a); rd2(id, A_CCW_LIMIT, &b);
  Serial.print("# "); printLabel(id); Serial.print(" servo limits: CW="); Serial.print(a); Serial.print(" CCW="); Serial.println(b);
}

void cmdTlimit(uint8_t id, int v) {
  if (v < 0 || v > 1023) { Serial.println("# 0..1023"); return; }
  if (!wr2(id, A_TORQUE_LIMIT, v)) { noResp(id); return; }
  Serial.print("# "); printLabel(id); Serial.print(" torque limit="); Serial.println(v);
}

void cmdFollow(int j, int sgn) {
  uint8_t L = LEADER_IDS[j], F = FOLLOWER_IDS[j];
  uint16_t cw = 0, ccw = 1023;
  if (!rd2(F, A_CW_LIMIT, &cw) || !rd2(F, A_CCW_LIMIT, &ccw)) { noResp(F); return; }
  if (cw == 0 && ccw == 0) { Serial.println("# follower is in wheel mode"); return; }
  int k = slotOf(F);
  int flo = imax(cw, cfg.lo[k]), fhi = imin(ccw, cfg.hi[k]);
  St l, f;
  if (!readSt(L, l)) { noResp(L); return; }
  if (!readSt(F, f)) { noResp(F); return; }
  int l0 = l.pos, f0 = f.pos, prevL = l.pos;
  wr2(F, A_SPEED, FOLLOW_SPEED);
  wr2(F, A_GOAL, f0);
  wr1(F, A_TORQUE, 1);
  Serial.print("# joint "); Serial.print(j + 1); Serial.print(" follow: goal = "); Serial.print(f0);
  Serial.print(" + ("); Serial.print(sgn); Serial.print(") * (leader - "); Serial.print(l0);
  Serial.print("), follower limit "); Serial.print(flo); Serial.print(".."); Serial.println(fhi);
  Serial.println("# move the leader. Stop: s+Enter");
  uint32_t nextP = 0, hiSince = 0;
  while (!keyHit()) {
    if (!readSt(L, l)) continue;
    if (abs(l.pos - prevL) > JUMP) {
      hold(F);
      Serial.print("# LEADER JUMPED "); Serial.print(prevL); Serial.print(" -> "); Serial.print(l.pos);
      Serial.println(" (dead zone?). Follower stopped, holding in place.");
      return;
    }
    prevL = l.pos;
    int raw = f0 + sgn * (l.pos - l0);
    int tgt = clampi(raw, flo, fhi);
    wr2(F, A_GOAL, tgt);
    if (!readSt(F, f)) continue;
    uint32_t now = millis();
    if (abs(f.load) > LOAD_STOP) {
      if (!hiSince) hiSince = now;
      else if (now - hiSince > LOAD_STOP_MS) { hold(F); Serial.println("# follower JAMMED/COLLIDED, stopped"); return; }
    } else {
      hiSince = 0;
    }
    if (now >= nextP) {
      nextP = now + 200;
      Serial.print("  L="); Serial.print(l.pos); Serial.print("  goal="); Serial.print(tgt);
      if (tgt != raw) Serial.print("(LIMIT)");
      Serial.print("  F="); Serial.print(f.pos); Serial.print("  error="); Serial.print(tgt - f.pos);
      Serial.print("  load="); Serial.print(f.load);
      if (f.pos < EDGE || f.pos > 1023 - EDGE || l.pos < EDGE || l.pos > 1023 - EDGE) Serial.print("  <AT DEAD ZONE EDGE>");
      printErr(lastErr);
      Serial.println();
    }
    delay(20);
  }
  Serial.println("# follow finished, follower holding in place. To release: t <id> 0");
}

// ---------------------------------------------------------------------------
// ID list or "all" (or empty) -> ids. All = in joint order L1 F1 L2 F2 ...
int parseIds(char **a, int na, uint8_t *ids) {
  if (na == 0 || !strcmp(a[0], "all")) {
    for (int j = 0; j < N_JOINT; j++) { ids[2 * j] = LEADER_IDS[j]; ids[2 * j + 1] = FOLLOWER_IDS[j]; }
    return N_SERVO;
  }
  int n = 0;
  for (int i = 0; i < na && n < N_SERVO; i++) {
    int v = atoi(a[i]);
    if (v < 0 || v > 253 || (v == 0 && a[i][0] != '0')) { Serial.print("# invalid ID: "); Serial.println(a[i]); return 0; }
    ids[n++] = v;
  }
  return n;
}

void handleLine(char *s) {
  char *tok[MAX_TOK];
  int nt = 0;
  for (char *p = strtok(s, " "); p && nt < MAX_TOK; p = strtok(NULL, " ")) tok[nt++] = p;
  if (!nt) return;
  Serial.print(">");
  for (int i = 0; i < nt; i++) { Serial.print(" "); Serial.print(tok[i]); }
  Serial.println();

  const char *c = tok[0];
  char **a = tok + 1;
  int na = nt - 1;
  int n1 = na > 0 ? atoi(a[0]) : -1, n2 = na > 1 ? atoi(a[1]) : -1, n3 = na > 2 ? atoi(a[2]) : -1, n4 = na > 3 ? atoi(a[3]) : -1;
  uint8_t ids[N_SERVO];

  if (!strcmp(c, "help") || !strcmp(c, "?")) cmdHelp();
  else if (!strcmp(c, "scan")) cmdScan();
  else if (!strcmp(c, "ids")) cmdIds();
  else if (!strcmp(c, "cfg")) cmdCfg();
  else if (!strcmp(c, "info") && na >= 1) cmdInfo(n1);
  else if (!strcmp(c, "led") && na >= 1) cmdLed(n1);
  else if (!strcmp(c, "comm") && na >= 1) cmdComm(n1, na >= 2 ? clampi(n2, 1, 5000) : 200);
  else if (!strcmp(c, "watch")) {
    bool all = na == 0 || !strcmp(a[0], "all");
    int n = parseIds(a, na, ids);
    if (n) watchIds(ids, n, all);
  }
  else if (!strcmp(c, "pair")) {
    int n = 0;
    for (int i = 0; i < na; i++) {
      int j = atoi(a[i]);
      if (j < 1 || j > N_JOINT) { Serial.println("# joint must be 1..5"); return; }
      ids[n++] = LEADER_IDS[j - 1];
      ids[n++] = FOLLOWER_IDS[j - 1];
    }
    if (!na) n = parseIds(a, 0, ids);
    watchIds(ids, n, true);
  }
  else if (!strcmp(c, "t") && na >= 2) {
    int v = atoi(a[na - 1]) ? 1 : 0;
    int n = parseIds(a, na - 1, ids);
    if (n) cmdTorque(ids, n, v);
  }
  else if (!strcmp(c, "go") && na >= 2) cmdGo(n1, n2, n3 > 0 ? clampi(n3, 1, 1023) : DEFAULT_SPEED);
  else if (!strcmp(c, "sweep") && na >= 3) cmdSweep(n1, n2, n3, n4 > 0 ? clampi(n4, 1, 1023) : DEFAULT_SPEED);
  else if (!strcmp(c, "home") && na >= 1) {
    int speed = DEFAULT_SPEED;
    if (a[na - 1][0] == 'h') { speed = clampi(atoi(a[na - 1] + 1), 1, 1023); na--; }   // trailing h<speed>
    int n = parseIds(a, na, ids);
    if (n) cmdHome(ids, n, speed);
  }
  else if (!strcmp(c, "sethome") && na >= 1) cmdSetHome(n1, na >= 2 ? n2 : -1);
  else if (!strcmp(c, "range") && na >= 2) cmdRange(n1, a[1], na >= 3 ? a[2] : NULL);
  else if (!strcmp(c, "limit") && na >= 3) cmdLimit(n1, n2, n3);
  else if (!strcmp(c, "tlimit") && na >= 2) cmdTlimit(n1, n2);
  else if (!strcmp(c, "follow") && n1 >= 1 && n1 <= N_JOINT) cmdFollow(n1 - 1, na >= 2 ? (n2 < 0 ? -1 : 1) : SIGN[n1 - 1]);
  else Serial.println("# not understood (missing argument?). 'help'");
}

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 5000);
  ph = dynamixel::PortHandler::getPortHandler(DEVICENAME);
  pk = dynamixel::PacketHandler::getPacketHandler(PROTOCOL_VERSION);
  if (!ph->openPort() || !ph->setBaudRate(BAUDRATE)) {
    while (1) { Serial.println("# ERROR: could not open the Dynamixel port"); delay(2000); }
  }
  Serial.println("# servo_lab ready. Torque untouched.");
  cfgLoad();
  cmdHelp();
}

void loop() {
  while (Serial.available()) {
    int c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (lineLen) { line[lineLen] = 0; handleLine(line); lineLen = 0; }
    } else if (lineLen < sizeof(line) - 1) {
      line[lineLen++] = (char)c;
    }
  }
}
