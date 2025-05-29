# plot_activity.py

import sqlite3
import pandas as pd
import matplotlib.pyplot as plt

# 1. Load & clean
conn = sqlite3.connect('activity.db')
df = pd.read_sql_query("SELECT timestamp, event, value FROM events WHERE event IN ('focus','idle')", conn)
conn.close()

df['value'] = pd.to_numeric(df['value'], errors='coerce')
df['time']  = pd.to_datetime(df['timestamp'], unit='s')

# 2. Compute focus durations
focus = df[df.event=='focus'].sort_values('time')
focus['duration_s'] = (focus.time.shift(-1) - focus.time).dt.total_seconds()
focus = focus[:-1]  # drop last incomplete
totals = focus.groupby('value').duration_s.sum().sort_values(ascending=False) / 3600

if totals.empty:
    print("No focus data to plot.")
    exit()

# 3. Plot bar chart
plt.figure(figsize=(8,6))
totals.head(10).plot.barh()
plt.gca().invert_yaxis()
plt.xlabel('Hours')
plt.title('Top 10 Active Windows')
plt.tight_layout()
plt.show()

# 4. Plot pie chart
idle = df[df.event=='idle']
idle_time = idle.value[idle.value>=5].sum() / 3600
active_time = totals.sum()

plt.figure(figsize=(6,6))
plt.pie([active_time, idle_time],
        labels=['Active (h)', 'Idle ≥5s (h)'],
        autopct='%1.1f%%', startangle=90)
plt.title('Active vs Idle')
plt.tight_layout()
plt.show()
