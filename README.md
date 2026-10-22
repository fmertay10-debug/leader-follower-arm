# arm

Leader-follower robot arm (Dynamixel AX-12A + OpenCM9.04) that learns to touch a dot on the table.

## Layout
- `vision/`  camera and dot detection
  - `aruco_make.py`  generates the corner markers (`aruco_markers.png`), ID0 top left, ID1 top right, ID2 bottom right, ID3 bottom left
  - `table_dot.py`   builds table coordinates (u, v: 0-1) from the markers and finds the white dot; `TableTracker` and `find_dot` are also used by other code
  - `common.py`      ArUco wrapper (OpenCV 4.6 and 4.7+)
- `firmware/servo_lab/servo_lab.ino`  OpenCM9.04: console for inspecting individual servos (scan, info, watch, go, sweep, pair, follow...)
- `firmware/arm_rec/arm_rec.ino`  OpenCM9.04: home (upright pose) + teleop + data stream (`D,...`) + button (`E,B`) + PC control (`G,...`)
- `robot/opencm.py`  PC-side serial link (`OpenCM`): reads the stream, sends commands/goals
- `tools/`   helpers (`record.py` DATA COLLECTION, `run_model.py` runs the model, `cam_test.py`, `grab_frame.py`, `joint_range.py` joint range/dead zone, `sidebyside.py`, old `red_dot.py`)
- `train/`   `prepare.py` (clean data, extract touch pose), `baseline.py` (model comparison)
- `data/`    recordings and test outputs

## Running
Iriun must be running (`iriunwebcam`); run from an Ubuntu terminal (not the VS Code snap terminal):

    python3 vision/table_dot.py 0

The camera number may change after a reboot: `grep . /sys/class/video4linux/*/name`
The camera and the table/markers must stay in place during and after recording.

## Data collection
Upload `firmware/arm_rec` to the OpenCM, then:

    python3 tools/record.py 0

Cycle: arms go home (upright) -> place the dot -> hold the leader, SPACE/button -> touch, lift ->
SPACE/button (arms return home) -> ENTER/button = save, d = discard. Episodes go to `data/episodes/ep_NNNN/`.
On screen the table is split into 4x4 cells, each showing the number of episodes in that region (red 0 = missing region).
Battery warning below 11.0 V, no new episode below 10.5 V (`--volt-warn`, `--volt-stop`).

## Running the model (training + test)
    .venv/bin/python train/prepare.py     # clean the episodes -> data/processed/dataset.npz + plots
    .venv/bin/python train/baseline.py    # (u, v) -> touch pose models, leave-one-out error
    .venv/bin/python tools/run_model.py --uv 0.3 0.5 --dry --speed 0.5   # plan without touching the arm
    .venv/bin/python tools/run_model.py 0 --speed 0.5                     # REAL ARM: camera + model
`.venv` = project virtual environment (torch CPU, numpy<2 pinned for the system OpenCV). x / ESC = emergency stop.

## Report
`Report.docx` (and `Report.pdf`) is the technical report of the project: math, figures, tables, code map.
To rebuild it: `bash report/build_all.sh` (generates the figures from the data, writes docx and pdf).
Scripts: `report/make_figures.py` (figures, equations), `report/build_report.py` (document), `report/toc_pages.py`.
The older Turkish version (`Rapor.docx`, `Rapor.pdf`, `RAPOR.md`) is kept as is and is no longer regenerated.
