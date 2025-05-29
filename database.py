# database.py
import sqlite3

DB_PATH = "activity.db"

def init_db(db_path=DB_PATH):
    """Initializes the database and creates the events table if it doesn't exist."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            event TEXT NOT NULL,
            value TEXT  -- For idle, it's duration (number); for focus, it's title (text)
        );
        '''
    )
    conn.commit()
    # For main.py, we return the connection.
    # For app.py, functions usually open/close their own.
    return conn

def log_event(conn, event_data):
    """Logs an event to the database."""
    if not conn:
        print("Database connection is not available for logging.")
        # Optionally, try to reconnect or handle error
        # temp_conn = sqlite3.connect(DB_PATH)
        # cursor = temp_conn.cursor()
        # ... then close temp_conn
        return

    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO events (timestamp, event, value) VALUES (?, ?, ?)",
            (event_data['timestamp'], event_data['event'], str(event_data['value']))
        )
        conn.commit()
    except sqlite3.Error as e:
        print(f"Error logging event: {e}")
        # Consider how to handle logging errors (e.g., retry, log to file)

# Example of how init_reports_table could be centralized if desired, though app.py handles it too.
def ensure_reports_table_exists(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS daily_reports (
            date TEXT PRIMARY KEY,
            output_json TEXT NOT NULL,
            last_run INTEGER
        );
        '''
    )
    # Ensure 'last_run' column exists (as in app.py)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(daily_reports);")
    cols = [row[1] for row in cur.fetchall()]
    if 'last_run' not in cols:
        cur.execute("ALTER TABLE daily_reports ADD COLUMN last_run INTEGER;")
    conn.commit()
    conn.close()