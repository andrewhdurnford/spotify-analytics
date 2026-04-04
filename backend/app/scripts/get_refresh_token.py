import os
from dotenv import load_dotenv
import base64
import httpx
from urllib.parse import urlencode

load_dotenv()

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = "http://127.0.0.1:8888/callback"

# Step 1: Get authorization URL
def get_auth_url():
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": "user-read-currently-playing user-read-playback-state user-read-recently-played playlist-modify-private playlist-modify-public",
    }
    return f"https://accounts.spotify.com/authorize?{urlencode(params)}"

# Step 2: Exchange code for tokens
def get_tokens(code):
    auth_header = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    
    response = httpx.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        headers={
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    
    return response.json()

if __name__ == "__main__":
    print("\n1. Visit this URL and authorize:")
    print(get_auth_url())
    print("\n2. After authorizing, you'll be redirected to 127.0.0.1:8888/callback?code=...")
    print("   Copy the 'code' parameter from the URL")
    
    code = input("\n3. Paste the code here: ").strip()
    
    tokens = get_tokens(code)
    
    if "refresh_token" in tokens:
        print("\n✅ Success! Add this to your .env file:")
        print(f"SPOTIFY_REFRESH_TOKEN={tokens['refresh_token']}")
    else:
        print("\n❌ Error:", tokens)