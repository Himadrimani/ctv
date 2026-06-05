# 📋 CHOICES.md — Engineering Decisions & Trade-off Analysis

> Documenting the key technical choices made during the design and implementation of PRIS, including model selection, schema design, and API architecture decisions.

---

## 1. Model Selection

### 1.1 Object Detection: YOLOv8 Nano (`yolov8n.pt`)

**Choice**: YOLOv8 Nano from Ultralytics.

**Alternatives Considered**:
| Model | Params | mAP (COCO) | CPU Inference | GPU Inference |
|---|---|---|---|---|
| **YOLOv8n (chosen)** | 3.2M | 37.3 | ~45ms | ~6ms |
| YOLOv8s | 11.2M | 44.9 | ~120ms | ~10ms |
| YOLOv8m | 25.9M | 50.2 | ~300ms | ~18ms |
| YOLOv5n | 1.9M | 28.0 | ~40ms | ~5ms |
| SSD MobileNet v2 | 6.8M | 22.0 | ~30ms | ~8ms |

**Rationale**:
- **CPU-first design**: The hackathon evaluation environment likely runs on CPU. YOLOv8n offers the best accuracy-to-speed ratio for CPU inference. At ~45ms per frame, it achieves ~22 FPS on a modern laptop CPU — sufficient for real-time retail analytics.
- **Built-in tracking support**: Ultralytics' `model.track(persist=True)` natively integrates ByteTrack/BoT-SORT without requiring separate tracker libraries.
- **Person detection accuracy**: Even the nano variant achieves strong person-class detection at the 0.35 confidence threshold used. Retail environments typically have clear line-of-sight to customers, compensating for the lower mAP.
- **Automatic model download**: The Ultralytics package auto-downloads weights on first run, eliminating manual model distribution.

**Trade-off**: YOLOv8n occasionally misses partially occluded persons in dense crowds. For production, upgrading to YOLOv8s (with GPU acceleration) would improve detection recall in congested checkout areas.

### 1.2 Multi-Object Tracking: ByteTrack

**Choice**: ByteTrack tracker via Ultralytics' `tracker="bytetrack.yaml"`.

**Alternatives Considered**:
- **BoT-SORT**: Higher accuracy but requires appearance feature extraction (Re-ID model), increasing latency.
- **DeepSORT**: Requires a separate Re-ID CNN, adding 15-20ms per frame overhead.
- **SORT**: Simpler but loses tracks frequently during occlusions.

**Rationale**:
- ByteTrack achieves near state-of-the-art tracking accuracy using only bounding box IoU matching — no appearance features required. This makes it lightweight enough for CPU execution.
- It handles temporary occlusions (person behind shelf) through a low-confidence detection matching stage, which is critical in retail environments with aisle obstructions.
- Seamless integration via Ultralytics' tracking API eliminates dependency management complexity.

### 1.3 Confidence Threshold: 0.35

**Choice**: Set `CONFIDENCE_THRESHOLD = 0.35` (lower than the typical 0.5 default).

**Rationale**: In retail CCTV footage, persons are often partially visible (entering/exiting frame edges, behind shelves). A lower threshold increases recall at the cost of occasional false positives. ByteTrack's association logic filters out most transient false detections, so the net effect is higher customer capture rate without noisy alerts.

---

## 2. Schema Design

### 2.1 Three-Table Relational Schema

**Choice**: Three separate SQLAlchemy models — `Camera`, `DetectionEvent`, `AnalyticsSnapshot`.

```
cameras (1) ──→ (N) detection_events
cameras (1) ──→ (N) analytics_snapshots
```

**Alternatives Considered**:
- **Single unified log table**: All data (metrics + alerts) in one table with a `record_type` discriminator.
- **Time-series database** (InfluxDB/TimescaleDB): Purpose-built for temporal metrics.
- **NoSQL document store** (MongoDB): Flexible schema for heterogeneous event data.

**Rationale**:
- **Separation of concerns**: Events and snapshots have fundamentally different query patterns. Events are filtered by category/severity with pagination; snapshots are queried as ordered time-series for charting. Separate tables allow independent indexing strategies.
- **SQLite simplicity**: Zero-configuration embedded database. No external service to install or manage. Ideal for hackathon evaluation where reviewers should be able to run the project immediately.
- **ORM benefits**: SQLAlchemy provides compile-time schema validation, automatic migration capabilities, and clean Pythonic query syntax. Pydantic response models (`from_attributes=True`) enable direct ORM-to-JSON serialization without manual mapping.

**Trade-off**: SQLite has limited concurrent write throughput (single writer lock). For multi-camera production deployments, migration to PostgreSQL is recommended (the SQLAlchemy ORM makes this a configuration change rather than a code rewrite).

### 2.2 DetectionEvent Schema Design

```python
class DetectionEvent:
    id:        Integer (PK, auto-increment)
    camera_id: Integer (FK → cameras.id, CASCADE delete)
    event_type: String  # LOITERING | CROWD_ALERT | QUEUE_ALERT
    severity:   String  # INFO | WARNING | CRITICAL
    message:    String  # Human-readable alert description
    timestamp:  DateTime (indexed)
```

**Design decisions**:
- **`event_type` as String vs Enum**: String was chosen for extensibility. New event types (e.g., `THEFT_ALERT`, `STAFF_ENTRY`) can be added without schema migration.
- **`severity` levels**: Three-tier severity aligns with standard operational alerting (monitoring dashboards, PagerDuty integration). CRITICAL events warrant immediate human intervention; WARNING events are informational but actionable.
- **Cascade delete on camera removal**: When a camera is deregistered, all its associated events and snapshots are automatically purged. This prevents orphaned records.
- **Timestamp indexing**: Events are frequently queried in reverse chronological order (most recent first). The timestamp index accelerates `ORDER BY timestamp DESC` queries.

### 2.3 AnalyticsSnapshot Schema Design

```python
class AnalyticsSnapshot:
    id:              Integer (PK, auto-increment)
    camera_id:       Integer (FK → cameras.id, CASCADE delete)
    total_occupancy: Integer
    queue_occupancy: Integer
    store_occupancy: Integer
    avg_dwell_time:  Float
    timestamp:       DateTime (indexed)
```

**Design decisions**:
- **5-second sampling interval**: Balances data granularity with storage efficiency. At 4 cameras × 1 snapshot/5s, the database grows at ~69,120 rows/day — manageable for SQLite.
- **Pre-aggregated metrics**: Rather than storing raw per-person coordinates and computing metrics at query time, snapshots store pre-computed aggregates (occupancy counts, average dwell time). This makes dashboard queries O(1) per data point.
- **Separate queue vs store occupancy**: Enables independent alerting thresholds and zone-specific analytics charting.

---

## 3. API Architecture Decisions

### 3.1 Framework: FastAPI

**Choice**: FastAPI with Pydantic v2 schemas.

**Alternatives Considered**:
- **Flask**: Simpler but lacks native async support, automatic OpenAPI docs, and Pydantic validation.
- **Django REST Framework**: Feature-rich but heavyweight for a real-time analytics API. ORM migration system adds unnecessary complexity for a SQLite prototype.
- **Express.js (Node)**: Would require maintaining a separate language runtime from the Python CV pipeline.

**Rationale**:
- **Automatic API documentation**: FastAPI generates interactive Swagger UI at `/docs` — critical for hackathon reviewers to explore and test the API without reading code.
- **Pydantic integration**: Request/response validation with type-safe schemas. `ConfigDict(from_attributes=True)` enables direct SQLAlchemy model → JSON response serialization.
- **Async lifespan**: FastAPI's `@asynccontextmanager` lifespan hook cleanly manages startup (DB creation, demo seeding, stream spawning) and shutdown (thread termination).
- **Streaming support**: `StreamingResponse` with MJPEG boundary natively supports live video streaming without WebSocket complexity.

### 3.2 MJPEG Streaming vs WebSocket

**Choice**: HTTP MJPEG streaming via `multipart/x-mixed-replace`.

**Alternatives Considered**:
- **WebSocket binary frames**: Lower overhead per frame but requires JavaScript client code.
- **HLS/DASH adaptive streaming**: Industry standard for video delivery but adds transcoding complexity and 2-10 second latency.
- **Server-Sent Events (SSE) with base64 frames**: Simple but base64 encoding inflates frame size by 33%.

**Rationale**:
- MJPEG streams can be embedded directly in HTML `<img src="url">` tags without any JavaScript. This is critical for Streamlit integration where `st.image(url)` renders the stream natively.
- Latency is sub-100ms (limited only by JPEG encoding time and network), making it suitable for "live" monitoring.
- Frame rate is throttled server-side (25 FPS for video, 20 FPS for heatmap) to prevent bandwidth saturation.

**Trade-off**: MJPEG sends full JPEG frames without inter-frame compression, resulting in higher bandwidth than H.264. For LAN-based retail deployments this is acceptable; for WAN access, an H.264 transcoding layer would be needed.

### 3.3 RESTful Resource Design

**Choice**: Resource-oriented REST API with clear noun-based endpoints.

| Design Decision | Choice | Rationale |
|---|---|---|
| URL structure | `/api/cameras/{id}/start` | Camera is the primary resource; actions are sub-resources. |
| Pagination | Query params (`limit`, `offset`) | Standard REST pagination pattern; defaults to 50 results. |
| Filtering | Query params (`event_type`, `severity`, `camera_id`) | Composable filters without complex request bodies. |
| Error handling | FastAPI `HTTPException` with status codes | 404 for missing cameras, 201 for creation, 204 for deletion. |
| CORS | Allow all origins (`*`) | Required for decoupled Streamlit frontend on different port. |

### 3.4 Thread Architecture vs Async Processing

**Choice**: Python `threading.Thread` with daemon mode for video processing.

**Alternatives Considered**:
- **`asyncio` coroutines**: Cannot run blocking OpenCV reads and YOLO inference without wrapping in `run_in_executor`, adding complexity.
- **`multiprocessing`**: Provides true parallelism (bypasses GIL) but complicates shared state access (requires `Manager` proxies or shared memory).
- **Celery task queue**: Over-engineered for a demo; adds Redis/RabbitMQ dependency.

**Rationale**:
- OpenCV's `VideoCapture.read()` releases the GIL during I/O wait, allowing true concurrent frame reading across camera threads.
- YOLO inference in PyTorch also releases the GIL during GPU tensor operations (and partially during CPU operations).
- Daemon threads automatically terminate when the main process exits, preventing zombie threads.
- `threading.Lock` provides simple, correct synchronization for the telemetry state shared between pipeline threads and API handlers.

**Trade-off**: Python's GIL limits true CPU parallelism. For >8 simultaneous camera feeds, migrating to `multiprocessing` with shared memory queues would be necessary.

### 3.5 Auto-Start on Application Boot

**Choice**: All active cameras auto-start their processing threads during FastAPI lifespan startup.

**Rationale**: Ensures that when a reviewer runs `uvicorn app.main:app`, the system immediately begins processing and generating data — no manual API calls required. Combined with demo camera pre-seeding, this achieves a true "zero-config" experience.

---

## 4. Frontend Architecture Decisions

### 4.1 Framework: Streamlit

**Choice**: Streamlit for the monitoring dashboard.

**Alternatives Considered**:
- **React/Next.js**: More flexible UI but requires separate build tooling and npm dependency management.
- **Grafana**: Purpose-built for metrics dashboards but less customizable and requires separate installation.
- **Flask + Jinja2 templates**: More control over HTML but significantly more boilerplate for interactive charts and data tables.

**Rationale**:
- **Single-file dashboard**: The entire UI is 430 lines of Python in `app.py`. No build step, no transpilation, no node_modules.
- **Native data science stack**: Direct integration with Pandas DataFrames, Plotly charts, and live data polling.
- **Interactive widgets**: Selectboxes, sliders, buttons, and auto-refresh loops are built-in primitives.
- **Custom CSS support**: `st.markdown(unsafe_allow_html=True)` enables premium Purplle-branded styling with glassmorphism-inspired metric cards.

### 4.2 Auto-Refresh Loop

**Choice**: `time.sleep(interval) → st.rerun()` loop with configurable 1-10 second interval.

**Rationale**: Streamlit lacks native real-time push updates. The sleep-rerun pattern provides near-real-time dashboard updates while keeping the implementation simple. The user can adjust the interval via sidebar slider to balance responsiveness with CPU usage.

---

## 5. Deployment Decisions

### 5.1 Docker Compose

**Choice**: Single Dockerfile with Docker Compose orchestration for two services (backend + dashboard).

**Rationale**:
- Ensures consistent environment across reviewer machines (no Python version conflicts, no missing OpenCV system libraries).
- Volume mount for `./data/` preserves SQLite database across container restarts.
- Single `docker-compose up --build` command for complete deployment.

### 5.2 SQLite Over Cloud Databases

**Choice**: Embedded SQLite rather than PostgreSQL or cloud-hosted databases.

**Rationale**: Zero external dependencies. No database server to install, configure, or connect to. The reviewer can clone the repo and have a working system in under 2 minutes. SQLAlchemy's ORM abstraction means production migration to PostgreSQL requires only a connection string change.

---

*Document authored as part of the Purplle Tech Challenge 2026 submission.*
