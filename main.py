# main.py
import sqlite3
from database import init_db, log_event
from watchers.windows import watch_windows
import os
from datetime import datetime

# Initialize both events and reports tables
conn = init_db()  # sets up events table
# Ensure daily_reports exists
def init_reports_table(db_path="activity.db"):
    conn2 = sqlite3.connect(db_path)
    conn2.execute(
        '''
        CREATE TABLE IF NOT EXISTS daily_reports (
            date TEXT PRIMARY KEY,
            output_json TEXT NOT NULL
        );
        '''
    )
    conn2.commit()
    conn2.close()

init_reports_table()

if __name__ == '__main__':
    # Reopen connection if needed
    conn = sqlite3.connect('activity.db') if not conn else conn
    try:
        for evt in watch_windows():
            ts = datetime.fromtimestamp(evt['timestamp']).strftime("[%H:%M:%S]")
            if evt['event'] == 'idle':
                print(f"{ts} idle for {evt['value']:.1f}s", flush=True)
            else:
                print(f"{ts} focus: {evt['value']}", flush=True)
            log_event(conn, evt)
    except KeyboardInterrupt:
        print("Stopping activity monitor...")
        conn.close()