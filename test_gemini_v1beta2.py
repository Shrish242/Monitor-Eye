import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    print("Error: GEMINI_API_KEY not found in environment variables.")
    exit()

MODEL = "gemini-1.5-flash-001" # Or "gemini-pro" for a common alternative
# Using the v1beta endpoint, which is more current than v1beta2
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={API_KEY}"

payload = {
    "contents": [
        {
            "parts": [{"text": "Write a short story about a robot learning emotions."}]
        }
    ]
}

headers = {
    "Content-Type": "application/json"
}

print(f"Requesting URL: {URL.replace(API_KEY, 'YOUR_API_KEY_HIDDEN')}") # Hide key in print

try:
    resp = requests.post(URL, headers=headers, json=payload)
    print("HTTP Status:", resp.status_code)

    if resp.status_code == 200:
        data = resp.json()
        # Check if 'candidates' exists and is not empty
        if data.get("candidates") and len(data["candidates"]) > 0:
            # It's good practice to check for 'content' and 'parts' as well
            # in case of safety blocks or other unexpected responses
            if data["candidates"][0].get("content") and data["candidates"][0]["content"].get("parts"):
                 print("Response Text:", data["candidates"][0]["content"]["parts"][0]["text"])
            else:
                print("Warning: 'content' or 'parts' missing in candidate.")
                print("Full candidate:", data["candidates"][0])
        else:
            print("Warning: No 'candidates' in response or candidates list is empty.")
            print("Full response data:", data)
    else:
        print("\nError from API:")
        print("Raw response headers:\n", resp.headers)
        print("Raw response text:\n", resp.text)

except requests.exceptions.RequestException as e:
    print("\nNetwork or Request Error:", e)
except Exception as e:
    print("\nError processing response (no JSON?):", e)
    if 'resp' in locals() and hasattr(resp, 'text'): # Check if resp exists and has text
        print("Raw response text:\n", resp.text)
    else:
        print("Response object not available or has no text.")