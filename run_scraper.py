"""Entry point: Start the scraper worker."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from workers.scraper_worker import run_scraper_worker

if __name__ == "__main__":
    run_scraper_worker()
