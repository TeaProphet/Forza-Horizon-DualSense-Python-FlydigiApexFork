# 🏎️ Forza Horizon - DualSense Adaptive Triggers (Flydigi Apex 4/5 Fork)

This repository is a fork of the original [Forza-Horizon-DualSense-Python](https://github.com/HamzaYslmn/Forza-Horizon-DualSense-Python) project, modified to add full telemetry rumble support for the **Flydigi Apex 4/5** controller in DualSense emulation mode.

> [!WARNING]
> This is a selfmade fork tested only on the **Flydigi Apex 4** controller running on **Windows 11**. Other models, connection types, or operating systems might work but have not been formally verified.

---

## 🛠️ What is Fixed and Added in this Fork

1. **Flydigi Apex 4/5 Telemetry Rumble**: The emulated DualSense firmware on the Apex 4/5 discards rumble values when trigger effects are sent in the same report. We solved this by splitting HID writes into back-to-back trigger and motor reports, enabling vibration support.
2. **Asphalt/Road Rumble Filtering**: Implemented a `rumble_surface_deadzone` (defaulting to `0.05`) to filter out the constant road surface vibration on clean asphalt. You will now only feel the heavy terrain rumble when driving off-road (dirt, gravel, grass, sand).
3. **Grip-Loss Rumble**: Added a `rumble_slip_deadzone` (defaulting to `0.10`) to filter out minor grip-based slip during normal cornering. Rumble triggers only when tires actually lose traction (wheelspin, slides, or drifting).
4. **Curbs and Strips**: Curb-strip collisions send distinct high-frequency vibrations directly to the light right motor.
5. **Interactive Live Tuning**: Added a dedicated **Telemetry Rumble (Flydigi Apex 4/5)** section to the Settings tab in the TUI, allowing you to configure all scales and deadzones interactively in real time.
6. **Robust Test Rumble Button**: Fixed the TUI's "Test Rumble" button by preventing the telemetry loop and idle checks from immediately overriding the test signal back to 0.

## 🎮 Flydigi DualSense Emulation Mode

Flydigi Space Station requires a supported game to be running (like *Marvel's Spider-Man: Miles Morales*) to activate the controller's DualSense emulation mode. 

**This project automates this process entirely!** 
When you launch the app, it automatically copies a tiny, safe system executable into a dummy process named `MilesMorales.exe` in the background. This is a trick to deceive Flydigi Space Station into immediately enabling DualSense emulation mode. When the app exits, the dummy background process is automatically cleaned up and closed. 

No manual steps, renaming, or separate `.exe` launches are required! (You can disable this behavior anytime by toggling `Auto-activate Flydigi DualSense emulation mode` off in the Settings tab).

---

## ⚙️ In-Game Setup

In Forza Horizon, open **Settings -> HUD and Gameplay** and scroll to the bottom:

- **Data Out**: ON
- **Data Out IP Address**: 127.0.0.1
- **Data Out IP Port**: 5300

---

## 🚀 Install and Launch Instructions

### Prerequisites
- Windows 10/11 or Linux.
- A Flydigi Apex 4/5 controller connected.
- **Flydigi Space Station 3/4** must be started before launching the utility.
- Inside Flydigi Space Station, navigate to **Adaptive Trigger** -> select **Marvel's Spider-Man: Miles Morales** -> change the trigger mode to **DS Mode (DualSense Mode)** -> set **Adaptive** to **ON**.

### How to Run (Using the Launcher)
1. Download this repository or place `win_start.bat` (Windows) / `linux_start.sh` (Linux) in your folder.
2. Double-click **`win_start.bat`** (or run `bash linux_start.sh` on Linux).
3. The launcher will automatically download the built bundle (`app/fhds.zuv.py`), install the `uv` package manager if missing, and boot the TUI interface.

### Running from Source (For Developers)
If you prefer to run the raw Python files directly:
1. Navigate to the `src/` directory.
2. Install dependencies and run using `uv`:
   ```powershell
   uv sync
   uv run main.py
   ```
