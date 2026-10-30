"""Entry point: Start the Media extraction worker."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from database.connection import init_db
from workers.media_worker import run_media_worker

if __name__ == "__main__":
    init_db()
    run_media_worker()
