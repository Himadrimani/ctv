from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.config import settings
from app.database import Base, engine, SessionLocal
from app.models import Camera
from app.routers.api import router as api_router
from app.pipeline.stream_manager import stream_manager

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Asynchronous lifecycle manager. Handles startup database tables creation,
    pre-populates demo camera channels, starts streams, and cleanly halts threads on shutdown.
    """
    print("[System Lifecycle] Booting Store Intelligence Systems...")
    # 1. Create tables in SQLite if they don't already exist
    Base.metadata.create_all(bind=engine)
    
    # 2. Insert default cameras if DB is empty
    db = SessionLocal()
    try:
        camera_count = db.query(Camera).count()
        if camera_count == 0:
            print("[System Lifecycle] Database empty. Pre-populating default demo cameras...")
            default_cams = [
                Camera(
                    id=1, 
                    name="Entrance & Main Floor Cam", 
                    stream_url="demo",  # triggers synthetic showroom canvas
                    is_active=True
                )
            ]
            db.add_all(default_cams)
            db.commit()
            print("[System Lifecycle] Default cameras seeded successfully.")
        
        # 3. Auto-start active cameras
        active_cameras = db.query(Camera).filter(Camera.is_active == True).all()
        for cam in active_cameras:
            stream_manager.start_stream(camera_id=cam.id, name=cam.name, stream_url=cam.stream_url)
            
    except Exception as e:
        print(f"[System Lifecycle] ERROR during startup: {e}")
        db.rollback()
    finally:
        db.close()

    yield  # Hand over control to FastAPI web server
    
    # 4. Clean up running camera threads on shutdown
    print("[System Lifecycle] Shutting down Store Intelligence Systems...")
    stream_manager.stop_all_streams()
    print("[System Lifecycle] Shutdown complete.")

# Initialize FastAPI App
app = FastAPI(
    title="Purplle Store Intelligence API",
    description="AI CCTV Visual Analytics API for retail optimization and customer flow analytics",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS (Cross-Origin Resource Sharing)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permits access from any domain (crucial for decoupled dashboard frontend)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(api_router)

@app.get("/")
def read_root():
    """System status check endpoint."""
    return {
        "status": "healthy",
        "service": "Purplle Store Intelligence Backend",
        "active_camera_threads": stream_manager.get_active_streams()
    }
