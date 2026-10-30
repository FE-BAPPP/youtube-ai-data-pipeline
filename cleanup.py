import sys
import os
sys.path.insert(0, os.getcwd())
from database.connection import get_db_connection

with get_db_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ocr_results WHERE video_id = %s", ("bl2m9eXfm_A",))
    cursor.execute("DELETE FROM frames WHERE video_id = %s", ("bl2m9eXfm_A",))
    cursor.execute("UPDATE videos SET status = %s WHERE video_id = %s", ("PENDING", "bl2m9eXfm_A"))
    conn.commit()
    print("Cleanup successful.")
