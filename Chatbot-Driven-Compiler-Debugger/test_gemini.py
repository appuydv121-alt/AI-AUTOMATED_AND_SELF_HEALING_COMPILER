import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
print("Key loaded:", bool(os.getenv("GEMINI_API_KEY")))
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
r = client.models.generate_content(model="gemini-3.1-flash-lite", contents="Reply with OK")
print(r.text)