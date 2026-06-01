from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict

# Camera Schemas
class CameraBase(BaseModel):
    name: str
    stream_url: str
    is_active: Optional[bool] = True

class CameraCreate(CameraBase):
    pass

class CameraResponse(CameraBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

# Detection Event Schemas
class DetectionEventResponse(BaseModel):
    id: int
    camera_id: int
    event_type: str
    severity: str
    message: str
    timestamp: datetime
    model_config = ConfigDict(from_attributes=True)

# Analytics Snapshot Schemas
class AnalyticsSnapshotResponse(BaseModel):
    id: int
    camera_id: int
    total_occupancy: int
    queue_occupancy: int
    store_occupancy: int
    avg_dwell_time: float
    timestamp: datetime
    model_config = ConfigDict(from_attributes=True)

# Dashboard Real-Time Telemetry Schema
class RealtimeAnalyticsResponse(BaseModel):
    camera_id: int
    total_occupancy: int
    queue_occupancy: int
    store_occupancy: int
    avg_dwell_time: float
    crowd_density_alert: bool
    queue_alert: bool
    active_loiterers_count: int
    recent_events: List[DetectionEventResponse]
    model_config = ConfigDict(from_attributes=True)
