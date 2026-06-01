import time
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.models import Camera, DetectionEvent, AnalyticsSnapshot
from app.schemas import CameraCreate, CameraResponse, DetectionEventResponse, AnalyticsSnapshotResponse, RealtimeAnalyticsResponse
from app.pipeline.stream_manager import stream_manager

router = APIRouter(prefix="/api")

# --- CAMERA FEED CONFIGURATION ENDPOINTS ---

@router.get("/cameras", response_model=List[CameraResponse])
def get_cameras(db: Session = Depends(get_db)):
    """Fetch all registered CCTV cameras from database."""
    return db.query(Camera).all()

@router.post("/cameras", response_model=CameraResponse, status_code=status.HTTP_201_CREATED)
def create_camera(camera: CameraCreate, db: Session = Depends(get_db)):
    """Register a new camera feed (RTSP stream link or local MP4 path)."""
    db_camera = Camera(name=camera.name, stream_url=camera.stream_url, is_active=camera.is_active)
    db.add(db_camera)
    db.commit()
    db.refresh(db_camera)
    
    # Automatically boot processing stream thread on creation if active
    if db_camera.is_active:
        stream_manager.start_stream(db_camera.id, db_camera.name, db_camera.stream_url)
        
    return db_camera

@router.delete("/cameras/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_camera(camera_id: int, db: Session = Depends(get_db)):
    """Deletes camera configuration and shuts down its processing thread."""
    db_camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not db_camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    
    # Terminate background thread
    stream_manager.stop_stream(camera_id)
    
    db.delete(db_camera)
    db.commit()
    return None

# --- STREAM CONTROLLER ENDPOINTS ---

@router.post("/cameras/{camera_id}/start")
def start_camera_stream(camera_id: int, db: Session = Depends(get_db)):
    """Manually triggers the background AI analyzer thread for this camera."""
    db_camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not db_camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    
    processor = stream_manager.start_stream(db_camera.id, db_camera.name, db_camera.stream_url)
    return {"status": "success", "message": f"Processing started for camera {db_camera.name}", "is_running": processor.is_running}

@router.post("/cameras/{camera_id}/stop")
def stop_camera_stream(camera_id: int, db: Session = Depends(get_db)):
    """Manually terminates the background AI analyzer thread for this camera."""
    db_camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not db_camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    
    stopped = stream_manager.stop_stream(camera_id)
    return {"status": "success", "message": f"Processing stopped for camera {db_camera.name}", "stopped": stopped}

# --- REAL-TIME VIDEO & HEATMAP MJPEG STREAMING ENDPOINTS ---

@router.get("/cameras/{camera_id}/live")
def get_live_mjpeg_stream(camera_id: int):
    """
    Returns a live multipart HTTP MJPEG video stream.
    Can be rendered directly in web browsers via <img src='/api/cameras/{id}/live'/>
    """
    # Verify camera exists in database
    db = SessionLocal()
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    db.close()
    
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    # Start the stream processor if it isn't running
    processor = stream_manager.get_processor(camera_id)
    if not processor:
        processor = stream_manager.start_stream(camera.id, camera.name, camera.stream_url)

    def frame_generator():
        while True:
            # Check if active
            proc = stream_manager.get_processor(camera_id)
            if not proc or not proc.is_running:
                # Stream stopped, exit generator
                break
                
            frame_bytes = proc.get_jpeg_frame()
            if frame_bytes:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            time.sleep(0.04)  # Throttle to approx 25 FPS to save bandwidth and cpu

    return StreamingResponse(
        frame_generator(), 
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

@router.get("/cameras/{camera_id}/live-heatmap")
def get_live_heatmap_stream(camera_id: int):
    """
    Returns a live multipart HTTP MJPEG activity heatmap video stream.
    """
    # Verify camera exists in database
    db = SessionLocal()
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    db.close()
    
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    processor = stream_manager.get_processor(camera_id)
    if not processor:
        processor = stream_manager.start_stream(camera.id, camera.name, camera.stream_url)

    def heatmap_generator():
        while True:
            proc = stream_manager.get_processor(camera_id)
            if not proc or not proc.is_running:
                break
                
            frame_bytes = proc.get_heatmap_jpeg()
            if frame_bytes:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            time.sleep(0.05)

    return StreamingResponse(
        heatmap_generator(), 
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

# --- ANALYTICS AND LOGGING ENDPOINTS ---

@router.get("/analytics/realtime/{camera_id}", response_model=RealtimeAnalyticsResponse)
def get_realtime_analytics(camera_id: int, db: Session = Depends(get_db)):
    """Retrieves current in-memory count telemetry along with the top 5 recent events."""
    processor = stream_manager.get_processor(camera_id)
    
    # 1. Fetch recent events from database
    recent_events = db.query(DetectionEvent)\
        .filter(DetectionEvent.camera_id == camera_id)\
        .order_by(DetectionEvent.timestamp.desc())\
        .limit(5)\
        .all()

    # 2. Extract values from live processor if active, otherwise return static zeroes
    if processor:
        with processor.lock:
            total_occ = processor.total_occupancy
            q_occ = processor.queue_occupancy
            s_occ = processor.store_occupancy
            avg_dwell = processor.avg_dwell_time
            loiter_count = processor.active_loiterers_count
            
            # Simple threshold checks for quick alert triggers
            from app.config import settings
            crowd_alert = total_occ >= settings.CROWD_DENSITY_THRESHOLD
            queue_alert = q_occ >= settings.QUEUE_CONGESTION_THRESHOLD
    else:
        total_occ = q_occ = s_occ = loiter_count = 0
        avg_dwell = 0.0
        crowd_alert = queue_alert = False

    return {
        "camera_id": camera_id,
        "total_occupancy": total_occ,
        "queue_occupancy": q_occ,
        "store_occupancy": s_occ,
        "avg_dwell_time": round(avg_dwell, 1),
        "crowd_density_alert": crowd_alert,
        "queue_alert": queue_alert,
        "active_loiterers_count": loiter_count,
        "recent_events": recent_events
    }

@router.get("/analytics/historical/{camera_id}", response_model=List[AnalyticsSnapshotResponse])
def get_historical_analytics(
    camera_id: int, 
    hours: int = Query(default=6, ge=1, le=48), 
    db: Session = Depends(get_db)
):
    """Retrieves a timeline of occupancy snapshots to plot historical line charts."""
    cutoff_time = datetime.utcnow() - timedelta(hours=hours)
    
    snapshots = db.query(AnalyticsSnapshot)\
        .filter(AnalyticsSnapshot.camera_id == camera_id, AnalyticsSnapshot.timestamp >= cutoff_time)\
        .order_by(AnalyticsSnapshot.timestamp.asc())\
        .all()
        
    return snapshots

@router.get("/events", response_model=List[DetectionEventResponse])
def get_events(
    camera_id: Optional[int] = None,
    event_type: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db)
):
    """Retrieves paginated detection alert events with customizable category filters."""
    query = db.query(DetectionEvent)
    
    if camera_id is not None:
        query = query.filter(DetectionEvent.camera_id == camera_id)
    if event_type:
        query = query.filter(DetectionEvent.event_type == event_type)
    if severity:
        query = query.filter(DetectionEvent.severity == severity)
        
    events = query.order_by(DetectionEvent.timestamp.desc())\
        .limit(limit)\
        .offset(offset)\
        .all()
        
    return events
