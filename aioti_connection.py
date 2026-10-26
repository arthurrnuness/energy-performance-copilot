import os
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("AIOTI_BASE_URL", "").rstrip("/")

def aioti_get(endpoint, params=None):
    token = get_aioti_token()

    response = requests.get(
        f"{BASE_URL}/{endpoint.lstrip('/')}",
        headers={
            "Authorization": f"Bearer {token}"
        },
        params=params,
        timeout=30
    )

    response.raise_for_status()
    return response.json()

def aioti_post(endpoint, payload):
    token = get_aioti_token()

    response = requests.post(
        f"{BASE_URL}/{endpoint.lstrip('/')}",
        headers={
            "Authorization": f"Bearer {token}"
        },
        json=payload,
        timeout=30
    )

    response.raise_for_status()
    return response.json()

def get_aioti_token():
    if not BASE_URL:
        raise RuntimeError("Set AIOTI_BASE_URL and credentials to enable live access")
    response = requests.post(
        f"{BASE_URL}/auth/login",
        json={
            "username": os.environ["AIOTI_USERNAME"],
            "password": os.environ["AIOTI_PASSWORD"]
        },
        timeout=30
    )

    response.raise_for_status()

    data = response.json()
    # Authentication response contents are not logged.
    #return None
    return data["token"]

#print(get_aioti_token())
if __name__ == "__main__":
    token = get_aioti_token()
    print("Authentication successful:", bool(token))