
import os, json, sqlite3, schedule, time
from datetime import date, timedelta
from win10toast import ToastNotifier
from dotenv import load_dotenv
load_dotenv()

from google import genai

# 1. Init client
GEMINI_KEY = "AIzaSyBEFHLMANSNyv6o6IogYutEhyY1nurAons"
if not GEMINI_KEY:
    raise RuntimeError("GEMINI_API_KEY not set")
client = genai.Client(api_key=GEMINI_KEY)

MODEL = "text-bison-001"

def load_yesterday_summary(db_path="activity.db"):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
      SELECT SUM(value) FROM events
      WHERE event='idle' AND DATE(timestamp, 'unixepoch')=?
    """, (yesterday,))
    idle_sec = cur.fetchone()[0] or 0
    cur.execute("""
      SELECT value, SUM(
        LEAD(timestamp) OVER (ORDER BY timestamp) - timestamp
      ) AS dur
      FROM events
      WHERE event='focus' AND DATE(timestamp, 'unixepoch')=?
      GROUP BY value
    """, (yesterday,))
    rows = cur.fetchall()
    conn.close()
    return {
        "date": yesterday,
        "idle_hours": idle_sec / 3600.0,
        "focus_hours": {r[0]: r[1]/3600.0 for r in rows}
    }

def analyze_with_gemini(summary):
    prompt = f"""
You are a productivity coach. Given this JSON summary:
```json
{json.dumps(summary, indent=2)} """