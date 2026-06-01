from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base

class Camera(Base):
    __tablename__ = "cameras"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String, nullable=False)
    stream_url = Column(String, nullable=False, default="demo")  # Can be RTSP, MP4, or "demo"
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    events = relationship("DetectionEvent", back_populates="camera", cascade="all, delete-orphan")
    snapshots = relationship("AnalyticsSnapshot", back_populates="camera", cascade="all, delete-orphan")


class DetectionEvent(Base):
    __tablename__ = "detection_events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    camera_id = Column(Integer, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String, nullable=False)  # LOITERING, CROWD_ALERT, QUEUE_ALERT, ENTRY
    severity = Column(String, nullable=False)    # INFO, WARNING, CRITICAL
    message = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationship
    camera = relationship("Camera", back_populates="events")


class AnalyticsSnapshot(Base):
    __tablename__ = "analytics_snapshots"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    camera_id = Column(Integer, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    total_occupancy = Column(Integer, default=0)
    queue_occupancy = Column(Integer, default=0)
    store_occupancy = Column(Integer, default=0)
    avg_dwell_time = Column(Float, default=0.0)  # Average seconds spent in frame
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationship
    camera = relationship("Camera", back_populates="snapshots")
