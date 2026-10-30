"""Entry point: Start the AI analysis worker."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from workers.ai_worker import run_ai_worker

if __name__ == "__main__":
    run_ai_worker()
