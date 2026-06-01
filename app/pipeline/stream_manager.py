import threading
from typing import Dict, List, Optional
from app.pipeline.detector import VideoProcessor

class StreamManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        """Implement Singleton Pattern to ensure a single coordinator manages all camera threads."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(StreamManager, cls).__new__(cls)
                cls._instance.active_processors = {}
                cls._instance.lock = threading.Lock()
        return cls._instance

    def start_stream(self, camera_id: int, name: str, stream_url: str) -> VideoProcessor:
        """
        Spawns a new background processing thread for the specified camera.
        If a thread is already active for this camera, it returns the existing instance.
        """
        with self.lock:
            if camera_id in self.active_processors:
                # Thread already running, check if it's healthy
                processor = self.active_processors[camera_id]
                if processor.is_running:
                    return processor
                else:
                    # Thread died or stopped, clean it up
                    processor.stop()
                    del self.active_processors[camera_id]

            # Initialize new processor
            processor = VideoProcessor(camera_id=camera_id, name=name, stream_url=stream_url)
            processor.start()
            self.active_processors[camera_id] = processor
            print(f"[Stream Manager] Started camera stream processing for ID {camera_id} ({name})")
            return processor

    def stop_stream(self, camera_id: int) -> bool:
        """Terminates the processing thread for a specific camera ID."""
        with self.lock:
            if camera_id in self.active_processors:
                processor = self.active_processors[camera_id]
                processor.stop()
                del self.active_processors[camera_id]
                print(f"[Stream Manager] Stopped camera stream processing for ID {camera_id}")
                return True
            return False

    def get_processor(self, camera_id: int) -> Optional[VideoProcessor]:
        """Retrieves the active VideoProcessor instance for a given camera ID."""
        with self.lock:
            return self.active_processors.get(camera_id)

    def get_active_streams(self) -> List[int]:
        """Returns a list of camera IDs that currently have active threads running."""
        with self.lock:
            return list(self.active_processors.keys())

    def stop_all_streams(self):
        """Safely shuts down all background camera processing threads during application termination."""
        with self.lock:
            print(f"[Stream Manager] Terminating all active streams ({len(self.active_processors)})...")
            for camera_id, processor in list(self.active_processors.items()):
                processor.stop()
            self.active_processors.clear()
            print("[Stream Manager] All active stream threads terminated successfully.")

# Global stream manager singleton instance
stream_manager = StreamManager()
