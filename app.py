# app.py
import os
import sqlite3
import time
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from openai import OpenAI
import json
from collections import defaultdict # Added

# Load your Gemini API key
GEMINI_API_KEY = 'AIzaSyCq9GdQMZFTLBzytLsnqhE4r3gf27Iai8Q'
if not GEMINI_API_KEY:
    raise RuntimeError("Please set the GEMINI_API_KEY environment variable.")

client = OpenAI(
    api_key=GEMINI_API_KEY,
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, 'activity.db')

# --- SCHEMA ENSURE FUNCTIONS (No changes here) ---
def ensure_reports_schema():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_reports (
            date TEXT PRIMARY KEY,
            output_json TEXT NOT NULL,
            last_run INTEGER
        );
    """)
    cur.execute("PRAGMA table_info(daily_reports);")
    cols = [row[1] for row in cur.fetchall()]
    if 'last_run' not in cols:
        cur.execute("ALTER TABLE daily_reports ADD COLUMN last_run INTEGER;")
    conn.commit()
    conn.close()

def ensure_events_schema():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            event TEXT NOT NULL,
            value TEXT
        );
        '''
    )
    conn.commit()
    conn.close()

# --- DATA QUERYING AND PROCESSING (Significant Changes/Additions) ---
def get_focus_events_for_timeseries(): # For the line chart on dashboard
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    today_start_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_ts = today_start_dt.timestamp()

    cur.execute("""
        SELECT timestamp, event, value
        FROM events
        WHERE timestamp >= ?
        ORDER BY timestamp ASC
    """, (today_start_ts,))
    raw_events = cur.fetchall()
    conn.close()

    hourly_focus_minutes = [0.0] * 24 
    last_focus_start_time = None

    for i, rec in enumerate(raw_events):
        current_ts = float(rec['timestamp'])
        
        if rec['event'] == 'focus':
            if last_focus_start_time is not None:
                # This logic is simplified for the timeseries chart; a more robust distribution
                # was in the previous version. For suggestions, we process events differently.
                # For this chart, we'll assume focus starts when 'focus' event hits.
                pass # Handled by next 'idle' or end of loop/next focus
            last_focus_start_time = current_ts

        elif rec['event'] == 'idle':
            if last_focus_start_time is not None:
                duration_seconds = current_ts - last_focus_start_time
                start_dt = datetime.fromtimestamp(last_focus_start_time)
                
                temp_start_dt = start_dt
                remaining_duration_seconds = duration_seconds
                while remaining_duration_seconds > 0 and temp_start_dt.timestamp() < time.time():
                    current_hour_index = temp_start_dt.hour
                    next_hour_dt = (temp_start_dt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
                    seconds_to_next_hour = (next_hour_dt - temp_start_dt).total_seconds()
                    
                    seconds_in_this_hour_block = min(remaining_duration_seconds, seconds_to_next_hour)

                    if 0 <= current_hour_index < 24:
                         hourly_focus_minutes[current_hour_index] += seconds_in_this_hour_block / 60.0

                    remaining_duration_seconds -= seconds_in_this_hour_block
                    if remaining_duration_seconds > 0:
                         temp_start_dt = next_hour_dt 
                    if temp_start_dt.day != start_dt.day and temp_start_dt > datetime.now():
                        break
            last_focus_start_time = None
    
    if last_focus_start_time is not None: # Ongoing focus
        duration_seconds = time.time() - last_focus_start_time
        start_dt = datetime.fromtimestamp(last_focus_start_time)
        
        temp_start_dt = start_dt
        remaining_duration_seconds = duration_seconds
        while remaining_duration_seconds > 0 and temp_start_dt.timestamp() < time.time():
            current_hour_index = temp_start_dt.hour
            next_hour_dt = (temp_start_dt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            seconds_to_next_hour = (next_hour_dt - temp_start_dt).total_seconds()

            seconds_in_this_hour_block = min(remaining_duration_seconds, seconds_to_next_hour)
            
            if 0 <= current_hour_index < 24:
                hourly_focus_minutes[current_hour_index] += seconds_in_this_hour_block / 60.0
            
            remaining_duration_seconds -= seconds_in_this_hour_block
            if remaining_duration_seconds > 0:
                temp_start_dt = next_hour_dt
            if temp_start_dt.day != start_dt.day and temp_start_dt > datetime.now():
                break
                
    current_hour = datetime.now().hour
    return hourly_focus_minutes[:current_hour + 1]


def query_events(): # For app breakdown and total idle/focus (last 24h)
    cutoff_time = time.time() - 24 * 3600
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT timestamp, event, value
        FROM events
        WHERE timestamp >= ?
        ORDER BY timestamp ASC
    """, (cutoff_time,))
    rows = cur.fetchall()
    conn.close()

    focus_secs_by_app = defaultdict(float)
    total_focus_seconds = 0.0
    
    active_focus_app = None
    active_focus_start_ts = None

    for rec in rows:
        ts = float(rec['timestamp'])
        
        if rec['event'] == 'focus':
            if active_focus_app is not None and active_focus_start_ts is not None: # End previous focus session
                duration = ts - active_focus_start_ts
                focus_secs_by_app[active_focus_app] += duration
                total_focus_seconds += duration
            active_focus_app = rec['value'] # Start new focus session
            active_focus_start_ts = ts
        elif rec['event'] == 'idle':
            if active_focus_app is not None and active_focus_start_ts is not None: # End current focus session before idle
                duration = ts - active_focus_start_ts
                focus_secs_by_app[active_focus_app] += duration
                total_focus_seconds += duration
            active_focus_app = None # Reset focus state
            active_focus_start_ts = None
            # Idle duration is directly in rec['value'], summed separately
    
    # Account for ongoing focus at the end of the period (up to now)
    if active_focus_app is not None and active_focus_start_ts is not None:
        duration = time.time() - active_focus_start_ts
        focus_secs_by_app[active_focus_app] += duration
        total_focus_seconds += duration

    # Get total idle seconds from explicit idle events
    conn_idle = sqlite3.connect(DB_PATH)
    cur_idle = conn_idle.cursor()
    cur_idle.execute("SELECT SUM(CAST(value AS REAL)) FROM events WHERE event = 'idle' AND timestamp >= ?", (cutoff_time,))
    sum_idle_val = cur_idle.fetchone()[0]
    conn_idle.close()
    idle_secs = sum_idle_val if sum_idle_val is not None else 0.0

    focus_list_for_api = [{'title': t, 'hours': s/3600.0}
                          for t, s in focus_secs_by_app.items() if s > 60] # Only apps used > 1 min

    return idle_secs, focus_list_for_api, total_focus_seconds


def get_raw_events_last_24h():
    cutoff_time = time.time() - 24 * 3600
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT timestamp, event, value
        FROM events
        WHERE timestamp >= ?
        ORDER BY timestamp ASC
    """, (cutoff_time,))
    rows = cur.fetchall()
    conn.close()
    return [{'timestamp': r['timestamp'], 'event': r['event'], 'value': r['value']} for r in rows]


def analyze_activity_patterns(events_24h):
    insights = []
    if not events_24h:
        return "No significant activity recorded in the last 24 hours to analyze for patterns."

    # --- Process events into distinct focus/idle sessions ---
    processed_sessions = []
    current_focus_app = None
    current_focus_start_ts = None

    for event_data in events_24h:
        ts = float(event_data['timestamp'])
        event_type = event_data['event']
        value = event_data['value']

        if event_type == 'focus':
            if current_focus_app and current_focus_start_ts: # End previous focus if any
                duration = ts - current_focus_start_ts
                if duration > 1: # Ignore very short flickers
                    processed_sessions.append({'type': 'focus', 'app': current_focus_app, 'start': current_focus_start_ts, 'end': ts, 'duration': duration})
            
            current_focus_app = value
            current_focus_start_ts = ts
        
        elif event_type == 'idle':
            if current_focus_app and current_focus_start_ts: # End current focus before idle
                duration = ts - current_focus_start_ts
                if duration > 1:
                    processed_sessions.append({'type': 'focus', 'app': current_focus_app, 'start': current_focus_start_ts, 'end': ts, 'duration': duration})
                current_focus_app = None # Reset focus state
                current_focus_start_ts = None

            try:
                idle_duration = float(value)
                if idle_duration > 1:
                     # Idle event's timestamp is its *end* time. Start is calculated.
                    idle_start_ts = ts - idle_duration 
                    processed_sessions.append({'type': 'idle', 'start': idle_start_ts, 'end': ts, 'duration': idle_duration})
            except ValueError:
                print(f"Warning: Could not parse idle duration: {value}")


    # Handle trailing focus session (if any, up to 'now')
    if current_focus_app and current_focus_start_ts:
        now_ts = time.time()
        duration = now_ts - current_focus_start_ts
        if duration > 1:
            processed_sessions.append({'type': 'focus', 'app': current_focus_app, 'start': current_focus_start_ts, 'end': now_ts, 'duration': duration})
    
    processed_sessions.sort(key=lambda x: x['start']) # Ensure chronological order

    if not processed_sessions:
        return "Not enough processed session data to analyze for patterns."

    # --- Analyze Processed Sessions ---
    hourly_focus_seconds = defaultdict(float)
    hourly_idle_seconds = defaultdict(float)

    for session in processed_sessions:
        start_dt = datetime.fromtimestamp(session['start'])
        remaining_duration = session['duration']
        current_segment_start_ts = session['start']

        while remaining_duration > 0.1:
            current_dt = datetime.fromtimestamp(current_segment_start_ts)
            hour_key = current_dt.hour 

            next_hour_start_dt = (current_dt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            seconds_to_next_hour = (next_hour_start_dt - current_dt).total_seconds()
            
            segment_duration = min(remaining_duration, seconds_to_next_hour)
            if segment_duration <= 0: break # Safety break

            if session['type'] == 'focus':
                hourly_focus_seconds[hour_key] += segment_duration
            elif session['type'] == 'idle':
                hourly_idle_seconds[hour_key] += segment_duration
            
            remaining_duration -= segment_duration
            current_segment_start_ts += segment_duration

    # Time of Day Patterns
    peak_focus_hours = sorted([h for h, s in hourly_focus_seconds.items() if s > 30 * 60], key=lambda h_key: hourly_focus_seconds[h_key], reverse=True)
    if peak_focus_hours:
        insights.append(f"User appears most focused around {peak_focus_hours[0]:02d}:00.")
        if len(peak_focus_hours) > 1:
             insights.append(f"Additional focused periods around {peak_focus_hours[1]:02d}:00.")

    high_idle_hours = sorted([h for h, s in hourly_idle_seconds.items() if s > 45 * 60], key=lambda h_key: hourly_idle_seconds[h_key], reverse=True)
    if high_idle_hours:
        insights.append(f"Significant idle time ({hourly_idle_seconds[high_idle_hours[0]]/3600:.1f}h) noted near {high_idle_hours[0]:02d}:00.")


    # Long Idle Periods
    for session in processed_sessions:
        if session['type'] == 'idle' and session['duration'] > 1.5 * 3600: # Longer than 1.5 hours
            idle_start_time_str = datetime.fromtimestamp(session['start']).strftime('%I:%M %p')
            insights.append(f"A long continuous idle period of {session['duration']/3600:.1f} hours started around {idle_start_time_str}.")
            break 

    # Context Switching / Short Focus Sessions
    focus_sessions = [s for s in processed_sessions if s['type'] == 'focus']
    if len(focus_sessions) > 15: # Only if there's a decent number of focus sessions
        short_focus_count = 0
        app_switches = 0
        for i in range(len(focus_sessions)):
            if focus_sessions[i]['duration'] < 5 * 60: # Less than 5 minutes
                short_focus_count +=1
            if i > 0 and focus_sessions[i]['app'] != focus_sessions[i-1]['app']:
                # Basic switch count, could be refined by checking if apps are "similar" or "distracting"
                app_switches +=1
        
        if short_focus_count > len(focus_sessions) * 0.6 and len(focus_sessions) > 5 : # If >60% of sessions are short
            insights.append(f"Many short focus sessions ({short_focus_count}) observed, suggesting potential for frequent interruptions or multitasking.")
        elif app_switches > 25 and len(focus_sessions) > 10: # Heuristic
             insights.append(f"Frequent switching between different applications ({app_switches} switches) noted.")


    # App Categories (Very Basic Example - Needs a proper mapping)
    # Define your APP_CATEGORIES_KEYWORDS map here if you want to use this.
    # E.g., APP_CATEGORIES_KEYWORDS = {"work": ["code", "visual studio", "outlook"], "social": ["facebook", "twitter", "discord"]}
    # ... (logic to categorize apps and sum time per category) ...
    # if time_on_social_media > 1 * 3600 and time_on_work_apps > 0:
    #     insights.append(f"Considerable time ({time_on_social_media/3600:.1f}h) on social/distraction apps. This might impact overall productivity if during work periods.")


    if not insights:
        return "General activity patterns observed. No specific strong deviations detected for targeted suggestions at this moment."
        
    return "\n- ".join(insights)

# --- GEMINI AND REPORTING FUNCTIONS ---
def upsert_report(date_str, output_json):
    now_ts = int(time.time())
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
       INSERT INTO daily_reports (date, output_json, last_run)
       VALUES (?, ?, ?)
       ON CONFLICT(date) DO UPDATE SET
         output_json=excluded.output_json,
         last_run=excluded.last_run
    """, (date_str, output_json, now_ts))
    conn.commit()
    conn.close()

def call_gemini(prompt: str) -> str:
    try:
        resp = client.chat.completions.create(
            model="gemini-1.5-flash-latest",
            messages=[
                {"role": "system",  "content": (
                    "You are an insightful and empathetic productivity assistant. "
                    "Your goal is to provide personalized, actionable advice based on the user's computer activity patterns. "
                    "Focus on positive framing and constructive suggestions. "
                    "Output 3-4 tips as a plain text bulleted list (each tip on a new line, no markdown characters like '*' or '-')."
                )},
                {"role": "user",    "content": prompt}
            ]
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[call_gemini] Error: {e}")
        return "Unable to fetch suggestions at this time due to an internal error."

def generate_suggestions():
    try:
        idle_s, focus_list_for_summary, total_focus_s = query_events()
        idle_h = idle_s / 3600.0
        total_focus_h = total_focus_s / 3600.0

        raw_events_24h = get_raw_events_last_24h()
        activity_patterns_summary = analyze_activity_patterns(raw_events_24h)
        
        # Build the detailed context for the LLM
        llm_context = f"User's Computer Activity Analysis (Last 24 Hours):\n\n"
        llm_context += f"Overall Summary:\n"
        llm_context += f"- Total Focus Duration: {total_focus_h:.2f} hours.\n"
        llm_context += f"- Total Idle Duration: {idle_h:.2f} hours.\n"

        if focus_list_for_summary:
            llm_context += "- Top Focused Applications (by duration):\n"
            for itm in sorted(focus_list_for_summary, key=lambda x: x['hours'], reverse=True)[:5]: # Top 5
                llm_context += f"  - {itm['title']} (approx. {itm['hours']:.1f} hours)\n"
        else:
            llm_context += "- No significant extended focus on specific applications recorded.\n"
        
        llm_context += f"\nKey Observed Patterns:\n- {activity_patterns_summary}\n\n"
        llm_context += (
            "Based on these observations, please provide 3-4 concise, actionable, and personalized productivity tips. "
            "Directly address the patterns mentioned. For instance, if 'frequent short focus sessions' are noted, suggest techniques like time blocking or minimizing notifications. "
            "If 'significant idle time during typical peak hours' is observed, suggest scheduling intentional breaks or reviewing energy management. "
            "If 'long focus on a specific productive app' is seen, acknowledge it positively. "
            "Aim to be helpful and encouraging."
        )
        
        # print(f"--- PROMPT FOR GEMINI ---\n{llm_context}\n-------------------------") # Uncomment for debugging the prompt
        
        sug_text = call_gemini(llm_context)
        upsert_report(datetime.now().strftime("%Y-%m-%d"), sug_text)

    except Exception as e:
        print(f"[generate_suggestions] Critical Error: {e}")
        upsert_report(datetime.now().strftime("%Y-%m-%d"), "Could not generate suggestions due to an application error.")


def prune_old_events(db_path=DB_PATH, days_to_keep=3): # Keep 3 days for robust 24h analysis
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cutoff = time.time() - days_to_keep * 24 * 3600
    cur.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
    deleted = cur.rowcount
    conn.commit()
    cur.execute("VACUUM;")
    conn.close()
    print(f"[prune_old_events] removed {deleted} rows, vacuum complete.")

# --- FLASK ROUTES ---
@app.route('/api/activity-data')
def activity_data():
    idle_s, focus_list, total_focus_s = query_events()
    hourly_focus_today_minutes = get_focus_events_for_timeseries() 
    date_str = datetime.now().strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT output_json, last_run FROM daily_reports WHERE date=?", (date_str,))
    report_row = cur.fetchone()
    conn.close()

    payload = {
        'idle_hours_24h': idle_s / 3600.0,
        'focus_apps_24h': focus_list,
        'total_focus_hours_24h': total_focus_s / 3600.0,
        'hourly_focus_today': hourly_focus_today_minutes,
        'suggestions': "Suggestions are being generated or are not yet available for today.",
        'suggestions_last_updated': None
    }

    if report_row:
        payload['suggestions'] = report_row[0]
        if report_row[1]:
            payload['suggestions_last_updated'] = datetime.fromtimestamp(report_row[1]).strftime('%Y-%m-%d %H:%M:%S')
    
    total_recorded_time_s = total_focus_s + idle_s
    payload['productivity_score'] = round((total_focus_s / total_recorded_time_s) * 100) if total_recorded_time_s > 0 else 0
    
    num_hours_data = len(hourly_focus_today_minutes)
    payload['hourly_focus_labels'] = [f"{h:02d}:00" for h in range(num_hours_data)]
    
    return jsonify(payload)

@app.route('/')
def index():
    return render_template('index.html')

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    ensure_events_schema()
    ensure_reports_schema()

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(prune_old_events, trigger='interval', hours=8, args=[DB_PATH, 3])
    scheduler.add_job(generate_suggestions, trigger='interval', hours=2, next_run_time=datetime.now() + timedelta(seconds=20)) # Generate on startup (after delay) and then less frequently
    scheduler.start()

    print("Flask app starting on http://127.0.0.1:5000. Personalized suggestions will be generated periodically.")
    app.run(debug=True, port=5000, use_reloader=False)