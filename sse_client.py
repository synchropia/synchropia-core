import json
import requests

url = "http://127.0.0.1:8000/api/v1/chat/stream"

response = requests.post(
    url,
    headers={"Accept": "text/event-stream"},
    json={
        "prompt": "can you tell me more about yourself",
        "provider": "google",
        "model": "gemini-2.5-flash",
        "temperature": 0.7,
        "max_tokens": 1024,
    },
    stream=True,
)

if response.status_code == 200:

    for line in response.iter_lines(decode_unicode=True):

        if not line:
            continue

        if not line.startswith("data: "):
            continue

        payload = json.loads(line[6:])

        event = payload.get("event")

        if event == "token":
            print(payload["token"], end="", flush=True)

else:
    print(response.text)