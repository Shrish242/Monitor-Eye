# app.py
import os
import sqlite3
import time
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from openai import OpenAI
import json
from collections import defaultdict
import traceback

# Load your Gemini API key
GEMINI_API_KEY = 'AIzaSyCg5Vzu6ifPySzmTrcsESq7OjblSpG5H9M' # WARNING: Hardcoding API keys is a security risk! Use environment variables.
if not GEMINI_API_KEY or "YOUR_GEMINI_API_KEY" in GEMINI_API_KEY: # Basic check
    raise RuntimeError("Please set a valid GEMINI_API_KEY.")

client = OpenAI(
    api_key=GEMINI_API_KEY,
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, 'activity.db')

# --- SCHEMA ENSURE FUNCTIONS (No changes) ---
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

# --- TITLE CATEGORIZATION (No changes from previous version) ---
def categorize_title(title):
    title_lower = title.lower()
    productive_keywords = [
    "visual studio", "code", "pycharm", "intellij", "eclipse", "android studio",
    "word", "excel", "powerpoint", "outlook", "onenote", "gimp", "photoshop",
    "illustrator", "premiere pro", "after effects", "blender", "autocad", "figma",
    "jira", "trello", "asana", "slack", "teams", "zoom", "google docs", "google meet",
    "google sheets", "google slides", "terminal", "command prompt", "powershell",
    "research", "documentation", "learning", "tutorial", "course", "study", "reading",
    "writing", "developing", "coding", "designing", "meeting", "planning", "work",
    "canva", "obsidian", "notion", "stack overflow", "github", "gitlab", "api", "debug"
]
    unproductive_keywords = [
    "netflix", "hulu", "disney+", "prime video", "plex", "kodi", "hbo max", "apple tv",
    "spotify", "apple music", "youtube music", "soundcloud",
    "steam", "epic games", "origin", "gog galaxy", "blizzard", "xbox", "playstation",
    "facebook", "twitter", "instagram", "tiktok", "reddit", "9gag", "pinterest", "snapchat",
    "discord", # Can be mixed, but leans social/gaming for many
    "game", "gaming", "movie", "series", "episode", "funny", "comedy", "entertainment", 
    "music video", "vlog", "stream", "live stream", "match", "highlights", "meme", "social",
    "amazon", "ebay", "shopping", "aliexpress"
    ]
    video_platforms = ["youtube", "vimeo", "twitch", "dailymotion"]

    for vp in video_platforms:
        if vp in title_lower:
            # Check if productive keywords are *also* in the title of the video platform page
            if any(kw in title_lower for kw in productive_keywords if kw not in ["youtube", "video"]): # Avoid self-match
                return "productive_video"
            # Check if unproductive keywords are also in the title
            if any(kw in title_lower for kw in unproductive_keywords if kw not in ["youtube", "video"]):
                return "unproductive" # Treat unproductive video as just unproductive

    for kw in productive_keywords:
        if kw in title_lower:
            return "productive"

    for kw in unproductive_keywords:
        if kw in title_lower:
            return "unproductive"

    for vp in video_platforms: # If it's a video platform not caught above
        if vp in title_lower:
            return "video_generic" 

    if any(browser_kw in title_lower for browser_kw in ["chrome", "firefox", "edge", "safari", "opera", "browser"]):
        return "neutral_browser"
    if any(os_kw in title_lower for os_kw in ["explorer", "finder", "settings", "control panel"]):
        return "neutral_os"
        
    return "neutral_other"

# --- DATA QUERYING AND PROCESSING (Significant Changes) ---

def _distribute_duration_to_hourly_slots(start_ts, duration_seconds, category, 
                                        hourly_slots_productive, hourly_slots_unproductive, 
                                        hourly_slots_neutral_other, hourly_slots_video_generic):
    start_dt = datetime.fromtimestamp(start_ts)
    temp_start_dt = start_dt
    remaining_duration_seconds = duration_seconds
    now_time = time.time() # Cache current time

    while remaining_duration_seconds > 0.1 and temp_start_dt.timestamp() < now_time:
        current_hour_index = temp_start_dt.hour
        
        # Ensure we don't go into the next day if the timeseries is only for "today"
        # And also ensure we are not processing future slots if the data is for today.
        if temp_start_dt.day != datetime.fromtimestamp(now_time).day and temp_start_dt.timestamp() > now_time:
             break # Crossed into a future day relative to "now"

        next_hour_dt = (temp_start_dt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        seconds_to_next_hour = (next_hour_dt - temp_start_dt).total_seconds()
        
        seconds_in_this_hour_block = min(remaining_duration_seconds, seconds_to_next_hour)
        # If the remaining duration would push us past "now", cap it at "now"
        if temp_start_dt.timestamp() + seconds_in_this_hour_block > now_time:
            seconds_in_this_hour_block = now_time - temp_start_dt.timestamp()

        if seconds_in_this_hour_block <= 0: break

        if 0 <= current_hour_index < 24:
            minutes_in_block = seconds_in_this_hour_block / 60.0
            if category == "productive" or category == "productive_video":
                hourly_slots_productive[current_hour_index] += minutes_in_block
            elif category == "unproductive":
                hourly_slots_unproductive[current_hour_index] += minutes_in_block
            elif category == "video_generic":
                hourly_slots_video_generic[current_hour_index] += minutes_in_block
            else: # neutral_browser, neutral_os, neutral_other
                hourly_slots_neutral_other[current_hour_index] += minutes_in_block
        
        remaining_duration_seconds -= seconds_in_this_hour_block
        if remaining_duration_seconds > 0.1:
             temp_start_dt = next_hour_dt
        else:
            break
        # Safety break for very small remaining durations or if somehow stuck
        if temp_start_dt.timestamp() >= start_ts + duration_seconds + 1 :
            break


def get_focus_events_for_timeseries():
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

    hourly_productive_minutes = [0.0] * 24 
    hourly_unproductive_minutes = [0.0] * 24
    hourly_neutral_other_minutes = [0.0] * 24 # Combined neutral and non-specific video
    hourly_video_generic_minutes = [0.0] * 24

    last_focus_start_time = None
    last_focus_title = None

    for i, rec in enumerate(raw_events):
        current_ts = float(rec['timestamp'])
        
        if rec['event'] == 'focus':
            if last_focus_start_time is not None and last_focus_title is not None:
                # End previous focus session
                duration_seconds = current_ts - last_focus_start_time
                category = categorize_title(last_focus_title)
                _distribute_duration_to_hourly_slots(last_focus_start_time, duration_seconds, category,
                                                     hourly_productive_minutes, hourly_unproductive_minutes,
                                                     hourly_neutral_other_minutes, hourly_video_generic_minutes)
            # Start new focus session
            last_focus_start_time = current_ts
            last_focus_title = rec['value']

        elif rec['event'] == 'idle':
            if last_focus_start_time is not None and last_focus_title is not None:
                # End current focus session before idle
                duration_seconds = current_ts - last_focus_start_time
                category = categorize_title(last_focus_title)
                _distribute_duration_to_hourly_slots(last_focus_start_time, duration_seconds, category,
                                                     hourly_productive_minutes, hourly_unproductive_minutes,
                                                     hourly_neutral_other_minutes, hourly_video_generic_minutes)
            last_focus_start_time = None # Reset focus state
            last_focus_title = None
    
    # Account for ongoing focus at the end of the period (up to now)
    if last_focus_start_time is not None and last_focus_title is not None:
        duration_seconds = time.time() - last_focus_start_time
        category = categorize_title(last_focus_title)
        _distribute_duration_to_hourly_slots(last_focus_start_time, duration_seconds, category,
                                             hourly_productive_minutes, hourly_unproductive_minutes,
                                             hourly_neutral_other_minutes, hourly_video_generic_minutes)
                
    current_hour = datetime.now().hour
    # Combine neutral_other and video_generic for simplicity in the chart for now, or keep separate
    for i in range(24):
        hourly_neutral_other_minutes[i] += hourly_video_generic_minutes[i]

    return {
        "productive": hourly_productive_minutes[:current_hour + 1],
        "unproductive": hourly_unproductive_minutes[:current_hour + 1],
        "neutral_other": hourly_neutral_other_minutes[:current_hour + 1]
    }


def query_events_for_summary_and_app_breakdown(): # Renamed for clarity
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

    focus_secs_by_app_title = defaultdict(float) # For doughnut chart
    categorized_focus_seconds = defaultdict(float)
    
    active_focus_app_title = None
    active_focus_start_ts = None

    for rec in rows:
        ts = float(rec['timestamp'])
        
        if rec['event'] == 'focus':
            if active_focus_app_title is not None and active_focus_start_ts is not None: # End previous
                duration = ts - active_focus_start_ts
                focus_secs_by_app_title[active_focus_app_title] += duration
                category = categorize_title(active_focus_app_title)
                categorized_focus_seconds[category] += duration
            active_focus_app_title = rec['value'] # Start new
            active_focus_start_ts = ts
        elif rec['event'] == 'idle':
            if active_focus_app_title is not None and active_focus_start_ts is not None: # End current
                duration = ts - active_focus_start_ts
                focus_secs_by_app_title[active_focus_app_title] += duration
                category = categorize_title(active_focus_app_title)
                categorized_focus_seconds[category] += duration
            active_focus_app_title = None 
            active_focus_start_ts = None
    
    if active_focus_app_title is not None and active_focus_start_ts is not None: # Ongoing focus
        duration = time.time() - active_focus_start_ts
        focus_secs_by_app_title[active_focus_app_title] += duration
        category = categorize_title(active_focus_app_title)
        categorized_focus_seconds[category] += duration

    conn_idle = sqlite3.connect(DB_PATH)
    cur_idle = conn_idle.cursor()
    cur_idle.execute("SELECT SUM(CAST(value AS REAL)) FROM events WHERE event = 'idle' AND timestamp >= ?", (cutoff_time,))
    sum_idle_val = cur_idle.fetchone()[0]
    conn_idle.close()
    idle_secs = sum_idle_val if sum_idle_val is not None else 0.0

    # For doughnut chart (top apps by raw title)
    app_list_for_doughnut = [{'title': t, 'hours': s/3600.0}
                             for t, s in focus_secs_by_app_title.items() if s > 60]

    # Categorized totals for efficiency score and Gemini
    total_productive_seconds = categorized_focus_seconds['productive'] + categorized_focus_seconds['productive_video']
    total_unproductive_seconds = categorized_focus_seconds['unproductive']
    total_neutral_seconds = (categorized_focus_seconds['neutral_browser'] + 
                             categorized_focus_seconds['neutral_os'] + 
                             categorized_focus_seconds['neutral_other'])
    total_video_generic_seconds = categorized_focus_seconds['video_generic']
    
    # For Gemini context, list some titles contributing to categories
    # This is already part of analyze_activity_patterns, so we'll rely on that.

    return (idle_secs, app_list_for_doughnut, 
            total_productive_seconds, total_unproductive_seconds, 
            total_neutral_seconds, total_video_generic_seconds)


def get_raw_events_last_24h(): # No change
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


def analyze_activity_patterns(events_24h): # Largely the same, ensure it uses the shared categorize_title
    # ... (previous implementation is mostly fine, ensure `categorize_title` is used for session categorization)
    # The `analysis_details` it returns is already quite good for Gemini.
    insights = [] 
    if not events_24h:
        return {
            "insights_string": "No significant activity recorded in the last 24 hours to analyze for patterns.",
            "details": {} 
        }

    processed_sessions = []
    current_focus_app = None
    current_focus_start_ts = None

    for event_data in events_24h:
        ts = float(event_data['timestamp'])
        event_type = event_data['event']
        value = event_data['value']

        if event_type == 'focus':
            if current_focus_app and current_focus_start_ts:
                duration = ts - current_focus_start_ts
                if duration > 1: 
                    processed_sessions.append({'type': 'focus', 'app': current_focus_app, 'start': current_focus_start_ts, 'end': ts, 'duration': duration, 'category': categorize_title(current_focus_app)})
            current_focus_app = value
            current_focus_start_ts = ts
        
        elif event_type == 'idle':
            if current_focus_app and current_focus_start_ts: 
                duration = ts - current_focus_start_ts
                if duration > 1:
                    processed_sessions.append({'type': 'focus', 'app': current_focus_app, 'start': current_focus_start_ts, 'end': ts, 'duration': duration, 'category': categorize_title(current_focus_app)})
                current_focus_app = None 
                current_focus_start_ts = None
            try:
                idle_duration = float(value)
                if idle_duration > 1:
                    idle_start_ts = ts - idle_duration 
                    processed_sessions.append({'type': 'idle', 'start': idle_start_ts, 'end': ts, 'duration': idle_duration})
            except ValueError:
                print(f"Warning: Could not parse idle duration: {value}")

    if current_focus_app and current_focus_start_ts:
        now_ts = time.time()
        duration = now_ts - current_focus_start_ts
        if duration > 1:
            processed_sessions.append({'type': 'focus', 'app': current_focus_app, 'start': current_focus_start_ts, 'end': now_ts, 'duration': duration, 'category': categorize_title(current_focus_app)})
    
    processed_sessions.sort(key=lambda x: x['start'])

    if not processed_sessions:
        return { "insights_string": "Not enough processed session data to analyze for patterns.", "details": {} }

    # --- Analyze Processed Sessions (Existing Logic adapted) ---
    # ... (Time of day patterns, long idle, context switching can remain similar) ...
    # The key is the detailed breakdown by category for Gemini:
    
    categorized_app_time = defaultdict(lambda: {'duration': 0.0, 'titles': set()})
    
    for session in processed_sessions:
        if session['type'] == 'focus':
            # Category is already in session from above
            category = session['category'] 
            categorized_app_time[category]['duration'] += session['duration']
            if len(categorized_app_time[category]['titles']) < 10:
                 categorized_app_time[category]['titles'].add(session['app'])

    analysis_details = {
        "productive_time_s": categorized_app_time['productive']['duration'] + categorized_app_time['productive_video']['duration'],
        "unproductive_time_s": categorized_app_time['unproductive']['duration'],
        "video_generic_time_s": categorized_app_time['video_generic']['duration'],
        "neutral_time_s": (categorized_app_time['neutral_browser']['duration'] + 
                             categorized_app_time['neutral_os']['duration'] + 
                             categorized_app_time['neutral_other']['duration']),
        "video_generic_titles": list(categorized_app_time['video_generic']['titles'])[:5],
        "top_unproductive_apps_titles": list(categorized_app_time['unproductive']['titles'])[:3],
        "top_productive_apps_titles": list(categorized_app_time['productive']['titles'] | categorized_app_time['productive_video']['titles'])[:3]
    }
    
    # Build insights string (example additions)
    if analysis_details['unproductive_time_s'] / 3600.0 > 1.0:
        insights.append(f"Significant time ({analysis_details['unproductive_time_s']/3600.0:.1f}h) on potentially unproductive activities.")
    if analysis_details['productive_time_s'] / 3600.0 > 0.5:
        insights.append(f"Productive work detected for about {analysis_details['productive_time_s']/3600.0:.1f}h.")

    final_insights_string = "\n- ".join(insights) if insights else "General activity patterns observed."
        
    return { "insights_string": final_insights_string, "details": analysis_details }


# --- GEMINI AND REPORTING FUNCTIONS ---
def upsert_report(date_str, output_json): # No change
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

def call_gemini(prompt: str) -> str: # No change in function, only in prompt content
    try:
        resp = client.chat.completions.create(
            model="gemini-1.5-flash-latest",
            messages=[
                {"role": "system",  "content": (
                    "You are an insightful and empathetic productivity assistant. "
                    "Your goal is to provide personalized, actionable advice based on the user's computer activity patterns. "
                    "Focus on positive framing and constructive suggestions. "
                    "The user's activity is categorized into 'Productive' (work, learning, educational video), "
                    "'Unproductive' (entertainment, games, non-educational videos, social media), "
                    "'Video Generic' (video platforms with unclear content from title), and 'Neutral' (OS, general browsing). "
                    "Provide specific, actionable tips to help the user optimize their productive time, manage unproductive distractions, "
                    "and improve their focus or work-life balance. "
                    "Acknowledge productive efforts. If 'Unproductive' or 'Video Generic' time is high, offer empathetic advice like "
                    "time-blocking for leisure, using website blockers, or choosing educational content. "
                    "Output 3-4 tips as a plain text bulleted list (each tip on a new line, NO markdown like '*' or '-')."
                )},
                {"role": "user",    "content": prompt}
            ]
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[call_gemini] Error: {e}")
        traceback.print_exc()
        return "Unable to fetch suggestions at this time due to an API or model error."

def generate_suggestions():
    try:
        (idle_s, _, # app_list_for_doughnut not directly needed for prompt, but calculated
         total_prod_s, total_unprod_s, 
         total_neutral_s, total_video_generic_s) = query_events_for_summary_and_app_breakdown()
        
        idle_h = idle_s / 3600.0
        total_focus_s = total_prod_s + total_unprod_s + total_neutral_s + total_video_generic_s

        raw_events_24h = get_raw_events_last_24h()
        analysis_result = analyze_activity_patterns(raw_events_24h) # This gives detailed breakdown
        # activity_patterns_summary_str = analysis_result["insights_string"] # We'll use the dict directly
        detailed_analysis = analysis_result["details"] # Contains categorized seconds and sample titles
        
        llm_context = f"User's Computer Activity Analysis (Last 24 Hours):\n\n"
        llm_context += f"Overall Time Summary:\n"
        llm_context += f"- Total Productive Focus: {detailed_analysis.get('productive_time_s', 0)/3600.0:.2f} hours.\n"
        if detailed_analysis.get('top_productive_apps_titles'):
             llm_context += f"  (Examples: {', '.join(detailed_analysis['top_productive_apps_titles'])})\n"
        llm_context += f"- Total Unproductive Focus: {detailed_analysis.get('unproductive_time_s', 0)/3600.0:.2f} hours.\n"
        if detailed_analysis.get('top_unproductive_apps_titles'):
             llm_context += f"  (Examples: {', '.join(detailed_analysis['top_unproductive_apps_titles'])})\n"
        llm_context += f"- Total Neutral/OS/Browser Focus: {detailed_analysis.get('neutral_time_s', 0)/3600.0:.2f} hours.\n"
        llm_context += f"- Total Generic Video Platform Focus (content ambiguous): {detailed_analysis.get('video_generic_time_s', 0)/3600.0:.2f} hours.\n"
        if detailed_analysis.get('video_generic_titles') and detailed_analysis.get('video_generic_time_s', 0) > 0:
            llm_context += f"  (Sample generic video titles: {', '.join(detailed_analysis['video_generic_titles'])})\n"
        llm_context += f"- Total Idle Duration: {idle_h:.2f} hours.\n\n"
        
        llm_context += f"Key Observed Patterns (automated textual analysis):\n- {analysis_result['insights_string']}\n\n"
        
        llm_context += (
            "Based on this detailed breakdown (especially productive vs. unproductive time and generic video use), "
            "please provide 3-4 concise, actionable, and personalized productivity tips. "
            "Frame suggestions positively and empathetically."
        )
        
        # print(f"--- PROMPT FOR GEMINI (length: {len(llm_context)}) ---\n{llm_context}\n-------------------------")
        
        sug_text = call_gemini(llm_context)
        upsert_report(datetime.now().strftime("%Y-%m-%d"), sug_text)

    except Exception as e:
        print(f"[generate_suggestions] Critical Error: {e}")
        traceback.print_exc()
        upsert_report(datetime.now().strftime("%Y-%m-%d"), "Could not generate suggestions due to an application error.")


def prune_old_events(db_path=DB_PATH, days_to_keep=3): # No change
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
    (idle_s, app_list_for_doughnut, 
     total_prod_s, total_unprod_s, 
     total_neutral_s, total_video_generic_s) = query_events_for_summary_and_app_breakdown()
    
    hourly_focus_categorized = get_focus_events_for_timeseries() 
    date_str = datetime.now().strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT output_json, last_run FROM daily_reports WHERE date=?", (date_str,))
    report_row = cur.fetchone()
    conn.close()

    total_focus_seconds = total_prod_s + total_unprod_s + total_neutral_s + total_video_generic_s
    total_recorded_time_s = total_focus_seconds + idle_s
    
    # New efficiency score based on productive time
    efficiency_score = round((total_prod_s / total_recorded_time_s) * 100) if total_recorded_time_s > 0 else 0

    payload = {
        'idle_hours_24h': idle_s / 3600.0,
        'focus_apps_24h': app_list_for_doughnut, # For the doughnut chart
        'total_focus_hours_24h': total_focus_seconds / 3600.0, # Overall focus
        'productive_focus_hours_24h': total_prod_s / 3600.0,
        'unproductive_focus_hours_24h': total_unprod_s / 3600.0,
        'neutral_or_other_focus_hours_24h': (total_neutral_s + total_video_generic_s) / 3600.0,

        'hourly_focus_productive_today': hourly_focus_categorized["productive"],
        'hourly_focus_unproductive_today': hourly_focus_categorized["unproductive"],
        'hourly_focus_neutral_other_today': hourly_focus_categorized["neutral_other"],
        
        'suggestions': "Suggestions are being generated or are not yet available for today.",
        'suggestions_last_updated': None,
        'productivity_score': efficiency_score # Updated score
    }

    if report_row:
        payload['suggestions'] = report_row[0]
        if report_row[1]:
            payload['suggestions_last_updated'] = datetime.fromtimestamp(report_row[1]).strftime('%Y-%m-%d %H:%M:%S')
    
    # Assuming the number of labels is driven by the longest array in hourly_focus_categorized
    # For simplicity, let's use productive, assuming all arrays will be same length up to current hour
    num_hours_data = len(hourly_focus_categorized["productive"])
    payload['hourly_focus_labels'] = [f"{h:02d}:00" for h in range(num_hours_data)]
    
    return jsonify(payload)

@app.route('/')
def index(): # No change
    return render_template('index.html')

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    ensure_events_schema()
    ensure_reports_schema()

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(prune_old_events, trigger='interval', days=1, args=[DB_PATH, 3]) 
    scheduler.add_job(generate_suggestions, trigger='interval', hours=1, next_run_time=datetime.now() + timedelta(seconds=45)) # Delay a bit more
    scheduler.start()

    print("Flask app starting on http://127.0.0.1:5000. Personalized suggestions will be generated periodically.")
    print("Ensure main.py (activity logger) is running separately to collect data.")
    app.run(debug=True, port=5000, use_reloader=False)