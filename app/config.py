import os
from typing import List, Tuple
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    # API & Server Configuration
    API_PORT: int = 8000
    API_HOST: str = "127.0.0.1"
    
    # Database Configuration
    DB_PATH: str = "data/store_intel.db"
    
    # AI Pipeline Configuration
    YOLO_MODEL: str = "yolov8n.pt"  # Lightweight nano model, fast on CPU
    DETECTION_CLASS_PERSON: int = 0  # YOLO class ID for person is 0
    CONFIDENCE_THRESHOLD: float = 0.35  # Person detection confidence threshold
    
    # Business Logic Alert Thresholds
    # Note: Keep thresholds low in code for high responsiveness in hackathon demos
    LOITERING_TIME_THRESHOLD: int = 15  # Time in seconds before triggering loitering warning
    CROWD_DENSITY_THRESHOLD: int = 4   # Number of concurrent people in frame to trigger crowd alert
    QUEUE_CONGESTION_THRESHOLD: int = 3 # Number of people in queue zone to trigger queue alert
    
    # Polygon Zone of Interest (ROI) for Queue Detection on a 1280x720 canvas
    # Format: List of (x, y) coordinates representing a polygon
    # This represents a designated cash register / queue area (usually bottom-left/center)
    QUEUE_ZONE_POLYGON: List[Tuple[int, int]] = Field(
        default=[(50, 250), (450, 250), (450, 680), (50, 680)]
    )
    
    # Main store zone polygon (the remaining area of interest)
    STORE_ZONE_POLYGON: List[Tuple[int, int]] = Field(
        default=[(480, 150), (1200, 150), (1200, 680), (480, 680)]
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()

# Ensure database directory exists
os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)
