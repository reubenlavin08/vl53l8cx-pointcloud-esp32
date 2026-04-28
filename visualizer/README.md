# 3D Point Cloud Visualiser

Live 3D point cloud rendering of the VL53L8CX sensor's 8×8 distance grid, projected through the sensor's true 45° field of view and coloured by distance using the `viridis` colormap.

<p align="center">
  <img src="../images/visualizer_point_cloud.png" width="760" alt="Live 3D point cloud — 64 zones projected into 3D space, coloured by distance"/>
</p>

Each dot is one of the sensor's 64 zones. The colour encodes distance: purple = close, yellow = far (up to 4000 mm by default). Points cluster together when they hit a flat surface at a similar distance, and spread apart when the sensor is pointed at a scene with depth variation.

---

## Setup

```bash
cd visualizer
python -m venv venv
venv\Scripts\activate         # Windows
pip install -r requirements.txt
```

---

## Running

1. Make sure the ESP32 is flashed with `STREAM_DATA = 1` in `main/main.c` (this is the default).
2. Make sure `idf.py monitor` is **closed** — only one program can hold the serial port at a time. Press `Ctrl + ]` to close it if open.
3. Run:

```bash
python visualizer.py --port COM12
```

Replace `COM12` with your actual port (check Device Manager under "Ports (COM & LPT)").

Use `Ctrl + C` in the terminal to stop.

---

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `COM12` | Serial port the ESP32 is on |
| `--baud` | `115200` | Must match the ESP-IDF default (115200) |
| `--max-mm` | `4000` | Z-axis range and colour scale maximum (mm) |

---

## How it works

### Sensor output
The ESP32 streams one line per frame over serial in the format:
```
DATA:820,815,801,790,785,780,775,770,...(64 values total)
```
Invalid or out-of-range zones are clamped to `MAX_DISTANCE_MM` (4000 mm) on the ESP32 side, so the visualiser always receives a full 64-value frame with no gaps.

### Geometric projection
The VL53L8CX has a 45° square field of view divided into an 8×8 grid of zones. Each zone subtends `45° / 8 = 5.625°` per side. At startup the visualiser precomputes a unit direction vector for every zone:

```
h_angle = (col − 3.5) × 5.625°   (horizontal, left/right)
v_angle = (row − 3.5) × 5.625°   (vertical, up/down)

x =  sin(h_angle)
y = −sin(v_angle)          (row 0 is top of sensor view)
z =  cos(h_angle) × cos(v_angle)  (depth, sensor faces +Z)
```

Each frame, multiplying these 64 unit vectors by their zone's measured distance gives the true 3D Cartesian coordinates of the point cloud.

### Temporal smoothing
Raw sensor readings vary ±10–30 mm frame-to-frame on a static scene. An exponential moving average (EMA) is applied per zone each frame:

```
smoothed = 0.3 × new_reading + 0.7 × previous_smoothed
```

This eliminates noise-driven flicker while still tracking real scene changes within ~3–4 frames (~300–400 ms). Adjust `EMA_ALPHA` at the top of `visualizer.py` to taste.

### In-place scatter update
The scatter object (all 64 points) is created once before the render loop. Each frame only the point positions and colours are updated in-place — the axes, labels, colorbar, and grid are never redrawn. This eliminates the flicker caused by `ax.clear()` in earlier versions and keeps the render fast enough to stay in sync with the 10 Hz sensor rate.

---

## Hardware this was tested on

| Component | Detail |
|-----------|--------|
| Sensor | STMicroelectronics SATEL-VL53L8CX |
| Microcontroller | ESP32-S3-DevKitC-1 (N16R8) |
| Serial chip | CH343 USB-UART, appears as COM12 |
| Connection | UART USB port (left port on DevKitC-1) |
| Baud rate | 115200 |
| Ranging config | 8×8 zones, 10 Hz, continuous mode |
