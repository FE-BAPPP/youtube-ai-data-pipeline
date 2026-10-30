"""Entry point: Start the FastAPI web application."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.app import app
import uvicorn
from utils.config import config

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=config.API_PORT)
