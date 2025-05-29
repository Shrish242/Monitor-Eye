import time
import win32gui
import win32process
from ctypes import Structure, windll, c_uint, sizeof, byref

class LASTINPUTINFO(Structure):
    _fields_ = [('cbSize', c_uint), ('dwTime', c_uint)]

def get_idle_time_seconds():
    li = LASTINPUTINFO()
    li.cbSize = sizeof(li)
    windll.user32.GetLastInputInfo(byref(li))
    return (windll.kernel32.GetTickCount() - li.dwTime) / 1000.0

def get_active_window_title():
    hwnd = win32gui.GetForegroundWindow()
    title = win32gui.GetWindowText(hwnd) or ""
    return title

def watch_windows(poll_interval=60.0, idle_threshold=1200.0):
    """
    Yields events every poll_interval seconds:
      - {'timestamp': t, 'event':'idle', 'value': idle_secs}
      - {'timestamp': t, 'event':'focus','value': title}
    Treats 'Program Manager' or empty title as idle (desktop active).
    Idle threshold is 10 minutes (600 seconds).
    """
    while True:
        now = time.time()
        idle_secs = get_idle_time_seconds()
        title = get_active_window_title()

        # Check if desktop is active (no app running)
        is_desktop = title in ('', 'Program Manager', 'No Active Window')

        if is_desktop or idle_secs >= idle_threshold:
            # User is idle
            yield {'timestamp': now, 'event': 'idle', 'value': idle_secs}
        else:
            # User is active with an app
            yield {'timestamp': now, 'event': 'focus', 'value': title}

        time.sleep(poll_interval)

if __name__ == "__main__":
    # Example usage: print events
    for event in watch_windows():
        ts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(event['timestamp']))
        print(f"{ts} - {event['event'].upper()}: {event['value']}")
