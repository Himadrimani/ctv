# 🏗️ DESIGN.md — Purplle Retail Store Intelligence System (PRIS)

> System architecture, design philosophy, and AI-assisted engineering decisions.

---

## 1. High-Level Architecture

PRIS follows a **layered, decoupled architecture** with four distinct tiers communicating through well-defined interfaces:

```
┌────────────────────────────────────────────────────────────┐
│                    Presentation Layer                       │
│              Streamlit Dashboard (Port 8501)                │
│    KPI Metrics · Live Video · Plotly Charts · Log Grid     │
└────────────────────────┬───────────────────────────────────┘
                         │  HTTP REST (JSON + MJPEG)
┌────────────────────────▼───────────────────────────────────┐
│                    API Gateway Layer                        │
│               FastAPI Server (Port 8000)                   │
│   Camera CRUD · Stream Control · Analytics · Events API    │
└────────────────────────┬───────────────────────────────────┘
                         │  Thread-safe shared state + SQLAlchemy ORM
┌────────────────────────▼───────────────────────────────────┐
│                  Core AI Pipeline Layer                     │
│       VideoProcessor (per-camera daemon thread)            │
│   YOLOv8 Detection · ByteTrack Tracking · ROI Zones       │
│   Heatmap Accumulation · Business Rules Engine             │
└────────────────────────┬───────────────────────────────────┘
                         │  SQLAlchemy writes
┌────────────────────────▼───────────────────────────────────┐
│                   Persistence Layer                         │
│           SQLite (data/store_intel.db)                      │
│   cameras · detection_events · analytics_snapshots         │
└────────────────────────────────────────────────────────────┘
```

### Design Principles

| Principle | Implementation |
|---|---|
| **Separation of Concerns** | Each layer has a single responsibility: detection, API exposure, storage, or visualization. |
| **Zero-Config Demo** | Simulation mode auto-activates when no video source is available, ensuring instant reviewer experience. |
| **Thread Safety** | All shared telemetry state uses `threading.Lock` for safe concurrent read/write across pipeline threads and API handlers. |
| **Graceful Degradation** | If YOLOv8 or OpenCV fails to load, the system falls back to simulation mode rather than crashing. |

---

## 2. Component Design

### 2.1 AI Pipeline (`app/pipeline/`)

**`VideoProcessor`** is the core engine. One instance per camera runs in its own daemon thread.

**Dual-mode processing:**
- **Real Video Mode**: Uses YOLOv8 (nano) for person detection and ByteTrack for multi-object tracking. Bounding boxes are projected onto ROI polygons using `cv2.pointPolygonTest()` for zone classification.
- **Simulation Mode**: Generates synthetic customer trajectories using a finite state machine (`SHOPPING → ENTERING_QUEUE → QUEUEING → EXITING`) on a dark-mode canvas. The downstream business logic, alert engine, and database writes operate identically in both modes.

**Key sub-systems within VideoProcessor:**
- **Heatmap Engine**: Maintains a low-resolution (180×320) floating-point accumulation grid. Each person's floor coordinate increments the grid. A leaky integrator (`×0.995` per frame) creates a smooth decay effect. The grid is Gaussian-blurred, colorized via JET colormap, and alpha-blended onto the video frame.
- **Business Rules Engine**: Evaluates three alert types (crowd density, queue congestion, individual loitering) every 5 seconds with cooldown timers to prevent database flooding.
- **Track Management**: Customer tracks are stored in `customer_tracks: Dict[int, dict]` keyed by tracker ID. Stale tracks (unseen for >3s) are pruned each frame.

**`StreamManager`** is a singleton thread coordinator. It manages the lifecycle (start, stop, health-check) of all `VideoProcessor` instances and exposes them to the API layer via `get_processor()`.

### 2.2 API Layer (`app/routers/api.py`)

Built on **FastAPI** with full OpenAPI/Swagger auto-documentation.

| Endpoint Group | Purpose |
|---|---|
| Camera CRUD (`/api/cameras`) | Register, list, and delete camera feed configurations. |
| Stream Control (`/api/cameras/{id}/start`, `/stop`) | Manually manage background processing threads. |
| Live Streams (`/api/cameras/{id}/live`, `/live-heatmap`) | HTTP MJPEG streaming via `StreamingResponse` with `multipart/x-mixed-replace`. |
| Analytics (`/api/analytics/realtime/{id}`, `/historical/{id}`) | In-memory telemetry snapshot + time-series history for Plotly charts. |
| Events (`/api/events`) | Paginated, filterable query endpoint for detection alert logs. |

### 2.3 Persistence Layer (`app/database.py`, `app/models.py`)

Three SQLAlchemy ORM models:
- **`Camera`**: Feed source configuration (name, stream URL, active status).
- **`DetectionEvent`**: Alert log entries with `event_type` (LOITERING, CROWD_ALERT, QUEUE_ALERT), `severity` (INFO, WARNING, CRITICAL), message, and timestamp.
- **`AnalyticsSnapshot`**: Periodic (every 5s) snapshots of occupancy metrics for historical trend analysis.

SQLite is chosen for zero-dependency deployment. `check_same_thread=False` is configured for safe multi-threaded access from pipeline threads and API handlers.

### 2.4 Dashboard (`app/dashboard/app.py`)

Built on **Streamlit** with:
- Real-time KPI metric cards with Purplle-branded styling.
- MJPEG video embedding for both annotated feed and heatmap views.
- Plotly interactive charts (line chart for traffic trends, bar chart for dwell times).
- Filterable event log data grid with severity-based color coding.
- Auto-refresh loop (configurable 1–10s interval) for live monitoring.

---

## 3. Data Flow

### Event Lifecycle

```
1. VideoProcessor detects persons in frame (YOLO or simulation)
2. Each person's floor coordinate is tested against ROI polygons
3. Track is created/updated in customer_tracks dictionary
4. Every 5 seconds:
   a. AnalyticsSnapshot saved to DB (occupancy counts, avg dwell)
   b. Business rules evaluated:
      - total_occupancy >= 4 → CROWD_ALERT (45s cooldown)
      - queue_occupancy >= 3 → QUEUE_ALERT (30s cooldown)
      - individual dwell_time > 15s → LOITERING (once per track)
   c. DetectionEvent records written to DB
5. Dashboard polls /api/analytics/realtime/{id} every N seconds
6. Historical charts query /api/analytics/historical/{id}
```

### Staff Exclusion Strategy

In production deployment, staff exclusion is handled through:
1. **Zone-Based Exclusion**: Staff-only zones (e.g., behind the cashier counter) can be defined as exclusion polygons. Any person detected within these zones is classified as staff and excluded from customer metrics.
2. **Appearance-Based Filtering**: With access to uniform color profiles, a lightweight color histogram classifier can be layered on top of YOLOv8 detections to distinguish staff from customers.
3. **Static Position Filtering**: Employees tend to remain static at known positions (registers, service desks). Tracks with near-zero displacement over extended periods at known staff positions are flagged as staff.

In the demo implementation, the system treats all detected persons as customers. The architecture is designed so that a staff classification filter can be inserted between the detection and zone-classification stages without modifying downstream logic.

### Re-Entry Handling

Customer re-entry is handled through ByteTrack's persistent ID assignment:
- ByteTrack maintains an internal Kalman filter state for each tracked object. If a person exits the frame temporarily (e.g., walks behind a shelf) and re-appears within a configurable timeout window, ByteTrack reassigns the same track ID.
- If a person fully exits and re-enters after the track expires (>3s unseen), they receive a new track ID and are counted as a new customer visit. This is the correct retail analytics behavior — each store visit should be counted independently.

### Edge-Case Handling

| Edge Case | Handling Strategy |
|---|---|
| Video stream disconnection | Auto-reconnect with 2s retry loop; falls back to simulation if source file is missing. |
| YOLO package unavailable | Graceful fallback to simulation mode with full logging. |
| Database write failures | Try/except with rollback; pipeline continues processing without interruption. |
| Alert flooding | Cooldown timers (30–45s) prevent duplicate alerts; loitering alerts fire once per track ID. |
| Empty database on startup | Auto-seeds 4 demo cameras during FastAPI lifespan startup event. |
| Video file loops | Local MP4 files automatically loop (`CAP_PROP_POS_FRAMES` reset) for continuous demo. |
| Concurrent dashboard access | `check_same_thread=False` + session-per-request pattern ensures safe concurrent reads. |

---

## 4. AI-Assisted Decisions

The following engineering and design decisions were made with AI assistance during the development of PRIS:

### 4.1 Architecture Design

**Decision**: Adopted a layered architecture with per-camera daemon threads managed by a singleton StreamManager.

**AI Contribution**: AI assisted in evaluating the trade-offs between asyncio-based coroutine processing vs. threading for the video pipeline. Given that OpenCV's `VideoCapture.read()` is a blocking I/O call and YOLOv8 inference is CPU-bound, threading was recommended over asyncio to avoid blocking the FastAPI event loop. The singleton pattern for StreamManager was suggested to prevent multiple coordinators from spawning conflicting threads.

### 4.2 Heatmap Design

**Decision**: Used a low-resolution (180×320) accumulation grid with leaky integrator decay and JET colormap visualization.

**AI Contribution**: AI helped design the heatmap approach. The initial design used full-resolution (1280×720) grids which caused excessive memory allocation and slow Gaussian blur operations. AI suggested downscaling to a smaller grid and upscaling after colorization — reducing compute by ~16× while maintaining visual quality. The decay factor of 0.995 per frame was tuned through AI-assisted experimentation to balance responsiveness with visual trail persistence.

### 4.3 Alert Cooldown System

**Decision**: Implemented per-event-type cooldown timers and per-track loitering deduplication.

**AI Contribution**: AI identified that the initial alerting logic (fire on every evaluation cycle) would flood the database with hundreds of duplicate events per minute. The cooldown timer pattern (45s for crowd, 30s for queue) was recommended, along with the `logged_loiterers` dictionary to ensure each customer triggers at most one loitering alert per visit.

### 4.4 Simulation Mode Design

**Decision**: Implemented a full state-machine-based customer simulation that produces frames visually resembling a retail store layout.

**AI Contribution**: AI assisted in designing the finite state machine (`SHOPPING → ENTERING_QUEUE → QUEUEING → EXITING`) and the path-finding logic for customer movement. The simulation was designed so that downstream processing (zone classification, alert evaluation, database writes) operates identically to real video mode — ensuring the demo accurately represents production behavior.

### 4.5 Zone Classification with Point-in-Polygon

**Decision**: Used OpenCV's `pointPolygonTest()` for classifying each detected person into Queue, Shopping, or Out-of-Bounds zones.

**AI Contribution**: AI recommended `cv2.pointPolygonTest()` over manual ray-casting implementations for polygon containment checks. This function is implemented in optimized C++ within OpenCV, providing significantly better performance for per-frame, per-person evaluations. AI also suggested using the bottom-center coordinate of each bounding box (where the person's feet touch the ground) rather than the center, as this provides more accurate spatial positioning for floor-level zone classification.

### 4.6 MJPEG Streaming Architecture

**Decision**: Used `StreamingResponse` with `multipart/x-mixed-replace` boundary for live video streaming.

**AI Contribution**: AI suggested the MJPEG streaming pattern over WebSocket-based frame transfer. MJPEG is natively supported by HTML `<img>` tags (allowing direct embedding in Streamlit via `st.image(url)`) without requiring any JavaScript WebSocket client code. This significantly simplified the dashboard implementation while maintaining low-latency visual updates.

### 4.7 Database Schema Decisions

**Decision**: Separated `DetectionEvent` and `AnalyticsSnapshot` into distinct tables rather than a single unified log table.

**AI Contribution**: AI recommended the separation because events (alerts) and snapshots (periodic metrics) have fundamentally different access patterns. Events are queried with category/severity filters and pagination (suited for a log-style table), while snapshots are queried as time-series data for charting (suited for a metrics table with timestamp indices). Combining them would require complex query filtering and degrade performance for both use cases.

### 4.8 Code Quality and Documentation

**AI Contribution**: AI assisted in writing comprehensive docstrings, inline comments, and this documentation. Error handling patterns (try/except/rollback/finally for database operations), logging messages, and configuration validation were refined with AI suggestions to ensure production-grade code quality.

---

## 5. Production Scaling Considerations

| Concern | Solution |
|---|---|
| **Multi-store deployment** | Replace SQLite with PostgreSQL; use schema-per-store or tenant column partitioning. |
| **Real-time alerting** | Integrate Redis Pub/Sub for instant push notifications to mobile/SMS. |
| **GPU acceleration** | Export YOLOv8 to TensorRT/ONNX FP16 for <5ms inference on NVIDIA Jetson. |
| **Frame throughput** | Implement frame-skipping (process every Nth frame) to reduce CPU load by 60-70%. |
| **Horizontal scaling** | Use message queues (RabbitMQ/Kafka) between capture, inference, and storage workers. |

---

*Document authored as part of the Purplle Tech Challenge 2026 submission.*
