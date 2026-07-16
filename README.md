# Nexus Attendance Agent

A standalone Windows desktop agent that runs real-time face-recognition attendance,
paired with the [Nexus Attendance SaaS](https://github.com/your-org/saas_ai_attendace_product) backend.

---

## How it works

```
Camera feed (2-5 fps)
  └─ Frame Capture (service/frame_capture.py)
       └─ Face Detection inside configured ROI (service/detection.py)
            └─ Face Recognition vs. local embedding cache (service/recognition.py)
                 └─ Debounce — one event per student per session window (service/debounce.py)
                      └─ SQLite queue (sync/queue.py)
                           └─ HTTP push to backend (sync/api_client.py)
```

Scheduling is driven by Windows Task Scheduler (`scheduler/task_scheduler.py`),
or by WebSocket commands from the admin dashboard (`scheduler/ws_listener.py`).

---

## Setup (end user)

1. Download the latest `AttendanceAgent.exe` from the [Releases](../../releases) page.
2. Double-click to run.  Click **More info → Run anyway** on the Windows SmartScreen
   dialog (the EXE is unsigned — this is expected).
3. The setup wizard opens:
   - **Step 1 — Token**: paste the Backend URL and the token from your Nexus dashboard
     (Settings → Cameras → New Agent).
   - **Step 2 — Camera**: select the camera to use and preview the feed.
   - **Step 3 — ROI**: draw the detection zone on the live frame.
   - **Step 4 — Model**: choose the AI model tier (auto-recommended).
4. The agent minimises to the system tray and starts marking attendance.

---

## Development

### Prerequisites

```bash
python -m pip install -r requirements.txt
```

### Run from source

```bash
python main.py
```

### Build the EXE locally (Windows only)

```bash
pip install pyinstaller
pyinstaller build/AttendanceAgent.spec --distpath dist --workpath build/work
```

### Build via GitHub Actions

Push a tag to trigger the automated build:

```bash
git tag v1.0.0
git push origin v1.0.0
```

The workflow (`.github/workflows/build.yml`) runs on `windows-latest`,
builds with PyInstaller, and attaches `AttendanceAgent.exe` to the release.

---

## Backend integration

The agent talks to four endpoints on the Nexus backend:

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/agent/verify-token` | Verify token + bind machine fingerprint |
| `POST` | `/api/agent/heartbeat` | Periodic subscription revalidation |
| `GET` | `/api/agent/sync-embeddings` | Pull face embeddings for enrolled students |
| `POST` | `/api/agent/push-attendance` | Batch-insert face-recognition events |

Admins generate tokens from the dashboard at **Settings → Cameras → New Agent**
(calls `POST /api/agent/tokens`).

---

## Folder structure

```
attendance-agent/
├── setup_wizard/   four-step setup GUI (customtkinter)
├── service/        frame capture, face detection, recognition, debounce
├── sync/           SQLite event queue + HTTP push/pull client
├── scheduler/      Windows Task Scheduler + WebSocket on-demand control
├── models/         ONNX weights (downloaded on first run, not in git)
├── config/         encrypted local config (Fernet, machine-fingerprint key)
├── build/          PyInstaller spec
└── .github/
    └── workflows/  build.yml — windows-latest CI producing AttendanceAgent.exe
```

---

## Licensing & machine binding

The agent token is issued per organisation from the Nexus dashboard.
On first `verify-token` call the server binds the token to the machine's
fingerprint (derived from hostname + MAC address).  Subsequent calls from a
different machine are rejected; the admin must revoke and reissue the token.

Camera limits are enforced per subscription tier:

| Plan | Max cameras |
|------|-------------|
| Free | 0 (not available) |
| Starter | 1 |
| Professional | 2 |
| Enterprise | 5+ (set per token) |

---

## Known limitations (current build)

- Setup wizard steps 2-4 are scaffolded but not yet functional.
- Face recognition engine and model download not yet implemented.
- System tray and background service not yet wired up.
- Windows SmartScreen warning is expected — EXE is unsigned.
