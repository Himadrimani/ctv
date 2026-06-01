import cv2
import numpy as np
import time
import os
import random
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import DetectionEvent, AnalyticsSnapshot, Camera

# Try loading YOLOv8, handle fallback gracefully
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False

class VideoProcessor:
    def __init__(self, camera_id: int, name: str, stream_url: str):
        self.camera_id = camera_id
        self.name = name
        self.stream_url = stream_url
        
        # State Management
        self.is_running = False
        self.thread: Optional[threading.Thread] = None
        self.latest_frame: Optional[np.ndarray] = None
        self.latest_heatmap_frame: Optional[np.ndarray] = None
        
        # Telemetry & Business Logic State
        self.total_occupancy = 0
        self.queue_occupancy = 0
        self.store_occupancy = 0
        self.avg_dwell_time = 0.0
        self.active_loiterers_count = 0
        
        # Tracking dictionaries
        # track_id -> {"first_seen": float, "last_seen": float, "zone": str, "last_alert_time": float}
        self.customer_tracks: Dict[int, dict] = {}
        
        # Heatmap grid (180x320 for performance, mapped to 1280x720 frame size)
        self.heatmap_grid = np.zeros((180, 320), dtype=np.float32)
        
        # Cooldown management for DB alerts to prevent flooding
        # Event type -> timestamp
        self.alert_cooldowns: Dict[str, float] = {
            "CROWD_ALERT": 0,
            "QUEUE_ALERT": 0,
        }
        
        # Track-specific loitering alerts logged
        # track_id -> timestamp
        self.logged_loiterers: Dict[int, float] = {}

        # YOLO Model (instantiated in the thread if running in real mode)
        self.model = None

        # Lock for thread-safe attribute reading
        self.lock = threading.Lock()

        # Simulated Customer State (used in "demo" mode)
        self.simulated_customers: List[dict] = []
        self.sim_customer_counter = 1
        
        # Initialize synthetic customers
        if self.stream_url == "demo":
            self._init_simulated_customers()

    def start(self):
        """Starts the video processing thread."""
        with self.lock:
            if not self.is_running:
                self.is_running = True
                self.thread = threading.Thread(target=self._run_pipeline, daemon=True)
                self.thread.start()

    def stop(self):
        """Stops the video processing thread."""
        with self.lock:
            self.is_running = False
        if self.thread:
            self.thread.join(timeout=2.0)

    def _init_simulated_customers(self):
        """Initializes simulated customers on a path-based state machine for the shop mockup."""
        # Pre-populate some customers at various stages
        for _ in range(5):
            self._spawn_simulated_customer()

    def _spawn_simulated_customer(self):
        """Spawns a customer with a custom trajectory."""
        cust_id = self.sim_customer_counter
        self.sim_customer_counter += 1

        # States: "SHOPPING", "ENTERING_QUEUE", "QUEUEING", "EXITING"
        state = "SHOPPING"
        
        # Spawn somewhere in the shopping zone (right side)
        x = random.randint(500, 1100)
        y = random.randint(180, 600)
        
        # Direct speed
        vx = random.uniform(-1.5, 1.5)
        vy = random.uniform(-1.5, 1.5)
        
        # Color palette: HSL pastel gradients (highly professional)
        color = (
            random.randint(180, 240), # B
            random.randint(100, 160), # G
            random.randint(200, 255)  # R
        )
        
        self.simulated_customers.append({
            "id": cust_id,
            "x": float(x),
            "y": float(y),
            "vx": vx,
            "vy": vy,
            "spawn_time": time.time(),
            "last_seen": time.time(),
            "state": state,
            "color": color,
            "queue_position": -1,
            "dwell_time": 0.0
        })

    def _update_simulated_customers(self):
        """State machine for custom pathing simulation."""
        now = time.time()
        
        # Define coordinates
        queue_x = 250
        register_y = 350
        
        active_queue_customers = [c for c in self.simulated_customers if c["state"] == "QUEUEING"]
        # Sort queue by Y coordinate (bottom to top waiting line)
        active_queue_customers.sort(key=lambda c: c["y"], reverse=True)
        for i, c in enumerate(active_queue_customers):
            c["queue_position"] = i

        for c in list(self.simulated_customers):
            c["dwell_time"] = now - c["spawn_time"]
            c["last_seen"] = now

            if c["state"] == "SHOPPING":
                # Random walk with shopping zone bounds
                c["x"] += c["vx"]
                c["y"] += c["vy"]
                
                # Aisle boundary collision
                if c["x"] < 480 or c["x"] > 1180:
                    c["vx"] *= -1
                if c["y"] < 150 or c["y"] > 650:
                    c["vy"] *= -1

                # Occasionally switch direction
                if random.random() < 0.05:
                    c["vx"] = random.uniform(-2.0, 2.0)
                    c["vy"] = random.uniform(-2.0, 2.0)

                # Transition to Queue after some shopping duration (e.g. 15-30 seconds)
                if c["dwell_time"] > random.uniform(15, 30):
                    c["state"] = "ENTERING_QUEUE"

            elif c["state"] == "ENTERING_QUEUE":
                # Path finding to queue zone
                target_x = queue_x
                # Position in line determines vertical target
                line_index = len([qc for qc in self.simulated_customers if qc["state"] == "QUEUEING"])
                target_y = register_y + (line_index * 60) # Stack upwards from the register counter
                
                dx = target_x - c["x"]
                dy = target_y - c["y"]
                dist = np.sqrt(dx**2 + dy**2)
                
                if dist > 5:
                    c["x"] += (dx / dist) * 4.0
                    c["y"] += (dy / dist) * 4.0
                else:
                    c["state"] = "QUEUEING"

            elif c["state"] == "QUEUEING":
                # Static in line, minor nervous sway (realistic tracking jitter)
                target_y = register_y + (c["queue_position"] * 55)
                dy = target_y - c["y"]
                c["y"] += dy * 0.1 # smoothly slide up in queue as line progresses
                c["x"] += random.uniform(-0.3, 0.3)
                
                # If at the front of queue (register) and has spent 8-12 seconds checking out, they exit
                if c["queue_position"] == 0 and c["dwell_time"] > random.uniform(35, 50):
                    c["state"] = "EXITING"

            elif c["state"] == "EXITING":
                # Walk towards the exit (bottom-left)
                target_x = 80
                target_y = 690
                dx = target_x - c["x"]
                dy = target_y - c["y"]
                dist = np.sqrt(dx**2 + dy**2)
                
                if dist > 8:
                    c["x"] += (dx / dist) * 5.0
                    c["y"] += (dy / dist) * 5.0
                else:
                    # Successfully left the store
                    self.simulated_customers.remove(c)
                    continue

        # Keep crowd numbers stable
        if len(self.simulated_customers) < 4:
            self._spawn_simulated_customer()
        elif len(self.simulated_customers) < 9 and random.random() < 0.02:
            self._spawn_simulated_customer()

    def _draw_synthetic_frame(self) -> np.ndarray:
        """Generates a premium dark mode visualization frame for the simulation."""
        # Create dark background frame (curated anthracite gray)
        frame = np.ones((720, 1280, 3), dtype=np.uint8) * 22
        
        # Draw elegant background grid lines
        grid_spacing = 80
        for i in range(0, 1280, grid_spacing):
            cv2.line(frame, (i, 0), (i, 720), (30, 30, 30), 1)
        for j in range(0, 720, grid_spacing):
            cv2.line(frame, (0, j), (1280, j), (30, 30, 30), 1)

        # Draw Queue Zone Polygon (Semi-transparent background overlay)
        overlay = frame.copy()
        q_poly = np.array(settings.QUEUE_ZONE_POLYGON, dtype=np.int32)
        cv2.fillPoly(overlay, [q_poly], (85, 45, 20)) # Dark Blue-Indigo
        cv2.polylines(frame, [q_poly], True, (230, 140, 50), 2) # Light blue boundary
        
        # Draw Store Shopping Zone Polygon
        s_poly = np.array(settings.STORE_ZONE_POLYGON, dtype=np.int32)
        cv2.fillPoly(overlay, [s_poly], (30, 70, 30)) # Dark forest green
        cv2.polylines(frame, [s_poly], True, (100, 220, 100), 2) # Light green boundary

        # Apply transparent zone blend (alpha blend)
        alpha = 0.35
        cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)

        # Annotate Zone Labels (Modern Minimalist Typography style)
        cv2.putText(frame, "CHECKOUT QUEUE ZONE", (settings.QUEUE_ZONE_POLYGON[0][0] + 15, settings.QUEUE_ZONE_POLYGON[0][1] - 15), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (230, 140, 50), 2, cv2.LINE_AA)
        
        cv2.putText(frame, "SHOPPING AISLES ZONE", (settings.STORE_ZONE_POLYGON[0][0] + 15, settings.STORE_ZONE_POLYGON[0][1] - 15), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (100, 220, 100), 2, cv2.LINE_AA)

        # Draw Store Features
        # Cashier Counter
        cv2.rectangle(frame, (180, 280), (320, 330), (50, 50, 50), -1)
        cv2.rectangle(frame, (180, 280), (320, 330), (80, 80, 80), 2)
        cv2.putText(frame, "CASHIER 1", (205, 312), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
        
        # Shopping Aisle Shelves
        cv2.rectangle(frame, (600, 250), (750, 550), (45, 45, 45), -1)
        cv2.rectangle(frame, (600, 250), (750, 550), (60, 60, 60), 2)
        cv2.putText(frame, "SHELF A", (645, 400), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (150, 150, 150), 2, cv2.LINE_AA)

        cv2.rectangle(frame, (900, 250), (1050, 550), (45, 45, 45), -1)
        cv2.rectangle(frame, (900, 250), (1050, 550), (60, 60, 60), 2)
        cv2.putText(frame, "SHELF B", (945, 400), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (150, 150, 150), 2, cv2.LINE_AA)

        # Entrance / Exit markers
        cv2.rectangle(frame, (1180, 550), (1275, 680), (30, 35, 30), -1)
        cv2.putText(frame, "ENTRANCE", (1190, 620), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 220, 100), 1, cv2.LINE_AA)
        
        cv2.rectangle(frame, (5, 550), (100, 680), (35, 30, 30), -1)
        cv2.putText(frame, "STORE EXIT", (10, 620), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 100, 255), 1, cv2.LINE_AA)

        # Draw customers in simulation
        for c in self.simulated_customers:
            x, y = int(c["x"]), int(c["y"])
            
            # Determine color and text based on loitering status
            border_color = c["color"]
            if c["dwell_time"] > settings.LOITERING_TIME_THRESHOLD:
                # Flash alert red for loitering
                border_color = (0, 0, 255) if int(time.time() * 2) % 2 == 0 else (50, 50, 255)
            
            # Draw customer dot and bounding box
            cv2.circle(frame, (x, y), 8, border_color, -1)
            cv2.circle(frame, (x, y), 12, (200, 200, 200), 1)

            # Draw simulated bounding box around dot
            w, h = 40, 90
            cv2.rectangle(frame, (x - w//2, y - h), (x + w//2, y), border_color, 2)
            
            # Bounding box tag
            label = f"ID: {c['id']} ({int(c['dwell_time'])}s)"
            cv2.rectangle(frame, (x - w//2, y - h - 18), (x + w//2, y - h), border_color, -1)
            cv2.putText(frame, label, (x - w//2 + 2, y - h - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1, cv2.LINE_AA)

        # Brand header bar
        cv2.rectangle(frame, (0, 0), (1280, 50), (10, 10, 10), -1)
        cv2.line(frame, (0, 50), (1280, 50), (60, 40, 80), 2)
        cv2.putText(frame, f"LIVE STORE VISION FEED | CAMERA: {self.name.upper()}", (20, 32), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 200, 255), 2, cv2.LINE_AA)
        
        # Add clock overlay
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(frame, timestamp_str, (1080, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (170, 170, 170), 1, cv2.LINE_AA)

        return frame

    def _run_pipeline(self):
        """The main threaded processing loop. Automatically runs detection and tracking."""
        print(f"[AI Pipeline] Starting camera feed processor: {self.name} (URL: {self.stream_url})")
        
        # 1. Initialize YOLOv8 if in real mode
        real_video_mode = False
        cap = None
        
        if self.stream_url != "demo":
            if YOLO_AVAILABLE:
                try:
                    # Set cache weights path to avoid cluttering workspace
                    self.model = YOLO(settings.YOLO_MODEL)
                    
                    # Try to open OpenCV Capture
                    cap = cv2.VideoCapture(self.stream_url)
                    if cap.isOpened():
                        real_video_mode = True
                        print(f"[AI Pipeline] Successfully opened OpenCV video source: {self.stream_url}")
                    else:
                        print(f"[AI Pipeline] WARNING: Failed to open video source {self.stream_url}. Falling back to simulation.")
                except Exception as e:
                    print(f"[AI Pipeline] Error initializing YOLOv8 / VideoCapture: {e}. Falling back to simulation.")
            else:
                print("[AI Pipeline] Ultralytics (YOLO) package is not available. Falling back to simulation.")
        
        # Set telemetry metrics update rate
        last_db_snapshot_time = 0.0
        frame_interval = 0.033  # target ~30 FPS
        
        # Queue and store polygon objects
        queue_poly = np.array(settings.QUEUE_ZONE_POLYGON, dtype=np.int32)
        store_poly = np.array(settings.STORE_ZONE_POLYGON, dtype=np.int32)
        
        frame_count = 0

        while True:
            # Check external stop request
            with self.lock:
                if not self.is_running:
                    break

            loop_start = time.time()
            frame_count += 1
            
            # --- PATH A: REAL VIDEO PROCESSING (YOLOv8 + ByteTrack) ---
            if real_video_mode and cap is not None:
                ret, frame = cap.read()
                if not ret:
                    # Loop video if it is a local file
                    if os.path.exists(self.stream_url):
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    else:
                        # Stream finished / disconnected
                        print(f"[AI Pipeline] Video stream disconnected. Retrying...")
                        time.sleep(2)
                        cap = cv2.VideoCapture(self.stream_url)
                        continue

                # Resize to standard size for faster inference & uniform canvas
                frame = cv2.resize(frame, (1280, 720))
                
                # To maintain high frame rates on CPUs, run detection every 2nd or 3rd frame (frame skipping)
                # But persist tracking boxes on skipped frames
                current_detections = []
                
                # Run YOLO Object Tracking
                # Persist=True triggers YOLOv8's built-in ByteTrack / BoT-SORT pipeline
                results = self.model.track(
                    source=frame,
                    persist=True,
                    classes=[settings.DETECTION_CLASS_PERSON],
                    conf=settings.CONFIDENCE_THRESHOLD,
                    tracker="bytetrack.yaml",
                    verbose=False
                )
                
                now_sec = time.time()
                active_ids_this_frame = set()
                
                # Parse tracking results
                if results and results[0].boxes is not None and results[0].boxes.id is not None:
                    boxes = results[0].boxes.xyxy.cpu().numpy()
                    track_ids = results[0].boxes.id.cpu().numpy().astype(int)
                    confidences = results[0].boxes.conf.cpu().numpy()
                    
                    # Process detected bounding boxes
                    for box, track_id, conf in zip(boxes, track_ids, confidences):
                        x1, y1, x2, y2 = map(int, box)
                        active_ids_this_frame.add(track_id)
                        
                        # Bottom-center coordinate (where person stands on floor)
                        bottom_center = (int((x1 + x2)/2), y2)
                        
                        # 1. Zone Classification via pointPolygonTest
                        in_queue = cv2.pointPolygonTest(queue_poly, bottom_center, False) >= 0
                        in_store = cv2.pointPolygonTest(store_poly, bottom_center, False) >= 0
                        
                        assigned_zone = "OUT_OF_BOUNDS"
                        if in_queue:
                            assigned_zone = "QUEUE"
                        elif in_store:
                            assigned_zone = "SHOPPING"

                        # 2. Update track statistics
                        if track_id not in self.customer_tracks:
                            self.customer_tracks[track_id] = {
                                "first_seen": now_sec,
                                "last_seen": now_sec,
                                "zone": assigned_zone,
                                "dwell_time": 0.0
                            }
                        else:
                            track_info = self.customer_tracks[track_id]
                            track_info["last_seen"] = now_sec
                            track_info["zone"] = assigned_zone
                            track_info["dwell_time"] = now_sec - track_info["first_seen"]

                        # 3. Add to Heatmap accumulation grid
                        # Scale full pixel coordinates into our small 180x320 grid space
                        grid_x = int((bottom_center[0] / 1280) * 319)
                        grid_y = int((bottom_center[1] / 720) * 179)
                        # Clamping
                        grid_x = max(0, min(grid_x, 319))
                        grid_y = max(0, min(grid_y, 179))
                        self.heatmap_grid[grid_y, grid_x] += 0.8  # accumulation weight

                        # 4. Draw overlays
                        border_color = (100, 220, 100) if assigned_zone == "SHOPPING" else (230, 140, 50)
                        dwell_time = self.customer_tracks[track_id]["dwell_time"]
                        
                        # Check loitering violation
                        if dwell_time > settings.LOITERING_TIME_THRESHOLD:
                            border_color = (0, 0, 255) # Red warning
                            
                        # Draw bounding box
                        cv2.rectangle(frame, (x1, y1), (x2, y2), border_color, 2)
                        # Draw tracking point
                        cv2.circle(frame, bottom_center, 6, border_color, -1)
                        
                        # Text tags
                        label = f"ID: {track_id} ({int(dwell_time)}s)"
                        cv2.rectangle(frame, (x1, y1 - 20), (x1 + 100, y1), border_color, -1)
                        cv2.putText(frame, label, (x1 + 3, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)

                # Prune inactive tracks that haven't been seen for > 3.0 seconds
                for tid in list(self.customer_tracks.keys()):
                    if now_sec - self.customer_tracks[tid]["last_seen"] > 3.0:
                        del self.customer_tracks[tid]

                # Update live telemetry metrics
                with self.lock:
                    self.total_occupancy = len(self.customer_tracks)
                    self.queue_occupancy = len([t for t in self.customer_tracks.values() if t["zone"] == "QUEUE"])
                    self.store_occupancy = len([t for t in self.customer_tracks.values() if t["zone"] == "SHOPPING"])
                    
                    # Dwell times averages
                    active_dwells = [t["dwell_time"] for t in self.customer_tracks.values()]
                    self.avg_dwell_time = float(np.mean(active_dwells)) if active_dwells else 0.0
                    
                    self.active_loiterers_count = len([
                        t for tid, t in self.customer_tracks.items() 
                        if t["dwell_time"] > settings.LOITERING_TIME_THRESHOLD
                    ])
                
                # Annotate zones polygons in live CCTV frame
                cv2.polylines(frame, [queue_poly], True, (230, 140, 50), 2)
                cv2.polylines(frame, [store_poly], True, (100, 220, 100), 2)
                
                # Brand Header Overlay
                cv2.rectangle(frame, (0, 0), (1280, 50), (10, 10, 10), -1)
                cv2.line(frame, (0, 50), (1280, 50), (60, 40, 80), 2)
                cv2.putText(frame, f"LIVE AI VIDEO STREAM | CAM: {self.name.upper()}", (20, 32), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 200, 255), 2, cv2.LINE_AA)
                timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cv2.putText(frame, timestamp_str, (1080, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (170, 170, 170), 1, cv2.LINE_AA)

                self.latest_frame = frame

            # --- PATH B: SIMULATION DEMO MODE ---
            else:
                self._update_simulated_customers()
                frame = self._draw_synthetic_frame()
                
                # Map simulated customers to backend telemetry structures
                now_sec = time.time()
                
                # Accumulate points on heatmap
                for c in self.simulated_customers:
                    # Bottom-center coordinate
                    grid_x = int((c["x"] / 1280) * 319)
                    grid_y = int((c["y"] / 720) * 179)
                    grid_x = max(0, min(grid_x, 319))
                    grid_y = max(0, min(grid_y, 179))
                    self.heatmap_grid[grid_y, grid_x] += 0.8  # accumulation weight

                # Extract simulated metrics
                q_count = len([c for c in self.simulated_customers if c["state"] in ["ENTERING_QUEUE", "QUEUEING"]])
                s_count = len([c for c in self.simulated_customers if c["state"] == "SHOPPING"])
                t_count = len(self.simulated_customers)
                dwell_times = [c["dwell_time"] for c in self.simulated_customers]
                avg_dwell = float(np.mean(dwell_times)) if dwell_times else 0.0
                loitering_count = len([c for c in self.simulated_customers if c["dwell_time"] > settings.LOITERING_TIME_THRESHOLD])

                # Update in-memory statistics
                with self.lock:
                    self.total_occupancy = t_count
                    self.queue_occupancy = q_count
                    self.store_occupancy = s_count
                    self.avg_dwell_time = avg_dwell
                    self.active_loiterers_count = loitering_count
                    
                    # Mirror track coordinates into customer_tracks dictionary so event engines process them identically
                    self.customer_tracks.clear()
                    for c in self.simulated_customers:
                        assigned_zone = "QUEUE" if c["state"] in ["ENTERING_QUEUE", "QUEUEING"] else "SHOPPING"
                        self.customer_tracks[c["id"]] = {
                            "first_seen": c["spawn_time"],
                            "last_seen": c["last_seen"],
                            "zone": assigned_zone,
                            "dwell_time": c["dwell_time"]
                        }

                self.latest_frame = frame

            # --- RENDER HEATMAP OVERLAY ---
            self._generate_heatmap_frame(frame)

            # --- CORE BUSINESS RULES ENGINE & ALERTS DB WRITER ---
            # Throttle database operations (e.g. log snapshots every 5 seconds, check alerts continuously)
            if loop_start - last_db_snapshot_time >= 5.0:
                self._save_analytics_snapshot()
                self._evaluate_and_log_alerts()
                last_db_snapshot_time = loop_start

            # Maintain stable target frames per second
            elapsed = time.time() - loop_start
            sleep_time = max(0.001, frame_interval - elapsed)
            time.sleep(sleep_time)
            
        # Clean up OpenCV captures
        if cap is not None:
            cap.release()
        print(f"[AI Pipeline] Stopped camera feed processor: {self.name}")

    def _generate_heatmap_frame(self, base_frame: np.ndarray):
        """Processes the normalized accumulative grid into a beautiful smooth color heatmap frame."""
        # 1. Decay the heatmap slightly over time to keep it dynamic and fresh (leaky integrator)
        self.heatmap_grid = self.heatmap_grid * 0.995

        # 2. Normalize grid to range [0, 255]
        max_val = np.max(self.heatmap_grid)
        if max_val > 0:
            normalized_grid = (self.heatmap_grid / max_val) * 255.0
        else:
            normalized_grid = self.heatmap_grid
            
        normalized_grid = normalized_grid.astype(np.uint8)

        # 3. Apply Gaussian blur to create a smooth, cloud-like thermal effect
        blurred = cv2.GaussianBlur(normalized_grid, (25, 25), 0)

        # 4. Colorize using OpenCV's JET colormap (Red = high density, Blue = low density)
        colored_heatmap = cv2.applyColorMap(blurred, cv2.COLORMAP_JET)

        # 5. Upscale small grid heatmap back to full 1280x720 canvas size
        colored_heatmap_resized = cv2.resize(colored_heatmap, (1280, 720))

        # 6. Overlay colorized map onto base frame, keeping dark background readable
        # Where blurred value is zero, keep the original background rather than overlaying solid blue
        mask = blurred > 2
        mask_resized = cv2.resize(mask.astype(np.uint8) * 255, (1280, 720))
        
        heatmap_overlay = base_frame.copy()
        
        # Weighted add on active hot spots
        cv2.addWeighted(colored_heatmap_resized, 0.6, base_frame, 0.4, 0, heatmap_overlay)
        
        # Apply mask
        final_heatmap = np.where(mask_resized[:, :, None] > 0, heatmap_overlay, base_frame)

        # Replace top brand bar to keep text legible on heatmap screen
        cv2.rectangle(final_heatmap, (0, 0), (1280, 50), (10, 10, 10), -1)
        cv2.line(final_heatmap, (0, 50), (1280, 50), (60, 40, 80), 2)
        cv2.putText(final_heatmap, f"LIVE ACTIVITY HEATMAP | CAM: {self.name.upper()}", (20, 32), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 200, 255), 2, cv2.LINE_AA)
        
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(final_heatmap, timestamp_str, (1080, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (170, 170, 170), 1, cv2.LINE_AA)

        self.latest_heatmap_frame = final_heatmap

    def _save_analytics_snapshot(self):
        """Saves a recurring database log of current store telemetry for historical analytics charts."""
        db: Session = SessionLocal()
        try:
            snapshot = AnalyticsSnapshot(
                camera_id=self.camera_id,
                total_occupancy=self.total_occupancy,
                queue_occupancy=self.queue_occupancy,
                store_occupancy=self.store_occupancy,
                avg_dwell_time=round(self.avg_dwell_time, 1),
                timestamp=datetime.utcnow()
            )
            db.add(snapshot)
            db.commit()
        except Exception as e:
            print(f"[Database Error] Failed to write analytics snapshot: {e}")
            db.rollback()
        finally:
            db.close()

    def _evaluate_and_log_alerts(self):
        """Evaluates business rules against current tracking state and logs events in the database."""
        now = time.time()
        db: Session = SessionLocal()
        
        try:
            # 1. CROWD DENSITY CHECK
            if self.total_occupancy >= settings.CROWD_DENSITY_THRESHOLD:
                if now - self.alert_cooldowns["CROWD_ALERT"] >= 45.0:  # 45 second cooldown
                    event = DetectionEvent(
                        camera_id=self.camera_id,
                        event_type="CROWD_ALERT",
                        severity="WARNING" if self.total_occupancy < settings.CROWD_DENSITY_THRESHOLD + 2 else "CRITICAL",
                        message=f"High crowd density! Detect {self.total_occupancy} customers in the store.",
                        timestamp=datetime.utcnow()
                    )
                    db.add(event)
                    self.alert_cooldowns["CROWD_ALERT"] = now
                    print(f"[Alert System] {event.message}")

            # 2. QUEUE CONGESTION CHECK
            if self.queue_occupancy >= settings.QUEUE_CONGESTION_THRESHOLD:
                if now - self.alert_cooldowns["QUEUE_ALERT"] >= 30.0:  # 30 second cooldown
                    event = DetectionEvent(
                        camera_id=self.camera_id,
                        event_type="QUEUE_ALERT",
                        severity="WARNING" if self.queue_occupancy == settings.QUEUE_CONGESTION_THRESHOLD else "CRITICAL",
                        message=f"Checkout counter congestion! {self.queue_occupancy} customers waiting in queue.",
                        timestamp=datetime.utcnow()
                    )
                    db.add(event)
                    self.alert_cooldowns["QUEUE_ALERT"] = now
                    print(f"[Alert System] {event.message}")

            # 3. INDIVIDUAL CUSTOMER LOITERING CHECK
            # Check if any tracked customer has exceeded the loitering duration
            for track_id, track in list(self.customer_tracks.items()):
                dwell = track["dwell_time"]
                if dwell > settings.LOITERING_TIME_THRESHOLD:
                    # Log alert once per customer track session to avoid database spamming
                    if track_id not in self.logged_loiterers:
                        event = DetectionEvent(
                            camera_id=self.camera_id,
                            event_type="LOITERING",
                            severity="WARNING",
                            message=f"Suspicious loitering detected! Customer ID {track_id} has lingered in the {track['zone'].lower()} area for {int(dwell)} seconds.",
                            timestamp=datetime.utcnow()
                        )
                        db.add(event)
                        self.logged_loiterers[track_id] = now
                        print(f"[Alert System] {event.message}")
            
            # Prune logged_loiterers for tracks that are no longer active
            active_ids = set(self.customer_tracks.keys())
            for logged_id in list(self.logged_loiterers.keys()):
                if logged_id not in active_ids:
                    del self.logged_loiterers[logged_id]
            
            db.commit()
        except Exception as e:
            print(f"[Database Error] Failed to process alerts: {e}")
            db.rollback()
        finally:
            db.close()

    def get_jpeg_frame(self) -> Optional[bytes]:
        """Encodes the latest raw OpenCV frame into JPEG format for MJPEG stream endpoint."""
        if self.latest_frame is None:
            return None
        ret, jpeg = cv2.imencode('.jpg', self.latest_frame)
        if not ret:
            return None
        return jpeg.tobytes()

    def get_heatmap_jpeg(self) -> Optional[bytes]:
        """Encodes the latest accumulated activity heatmap frame into JPEG format."""
        if self.latest_heatmap_frame is None:
            return None
        ret, jpeg = cv2.imencode('.jpg', self.latest_heatmap_frame)
        if not ret:
            return None
        return jpeg.tobytes()
