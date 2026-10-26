"""YouTube refresh token almaq üçün — öz kompüterinizdə (iMac) bir dəfə işə salın.

  pip3 install google-auth-oauthlib
  python3 youtube_auth.py client_secret.json

Brauzer açılır, YouTube kanalınızın hesabı ilə daxil olub icazə verin.
Çıxan YOUTUBE_REFRESH_TOKEN-i serverdəki .env faylına yazın.
"""
import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
path = sys.argv[1] if len(sys.argv) > 1 else "client_secret.json"
flow = InstalledAppFlow.from_client_secrets_file(path, SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
cfg = json.load(open(path))
cfg = cfg.get("installed") or cfg.get("web")
print("\n.env üçün:\n")
print(f"YOUTUBE_CLIENT_ID={cfg['client_id']}")
print(f"YOUTUBE_CLIENT_SECRET={cfg['client_secret']}")
print(f"YOUTUBE_REFRESH_TOKEN={creds.refresh_token}")
