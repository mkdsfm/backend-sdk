"""
Helped functions for authentication.
"""

from pathlib import Path
from typing import Union

import requests
import yaml
from appdirs import user_config_dir
from yarl import URL

from requests.auth import HTTPBasicAuth
from urllib.parse import urlencode

CREDENTIALS_FILE = "credentials.yaml"


def get_access_token(baseurl: Union[str, URL], api_token: str, api_secret: str) -> str:
    """
    Fetch the JWT access token.
    """
    if isinstance(baseurl, str):
        baseurl = URL(baseurl)

    response = requests.post(
        baseurl / "v1/auth/",
        json={"name": api_token, "secret": api_secret},
        headers={"Content-Type": "application/json"},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    return payload["payload"]["access_token"]


def get_credentials_path() -> Path:
    """
    Return the system-dependent location of the credentials.
    """
    config_dir = Path(user_config_dir("preset-cli", "Preset"))
    return config_dir / CREDENTIALS_FILE


def store_credentials(
    api_token: str,
    api_secret: str,
    manager_url: URL,
    credentials_path: Path,
) -> None:
    """
    Store credentials.
    """
    credentials_path.parent.mkdir(parents=True, exist_ok=True)

    credentials = {
        "api_token": api_token,
        "api_secret": api_secret,
        "baseurl": str(manager_url),
    }

    while True:
        store = input(f"Store the credentials in {credentials_path}? [y/N] ")
        if store.strip().lower() == "y":
            with open(credentials_path, "w", encoding="utf-8") as output:
                yaml.safe_dump(credentials, output)
            credentials_path.chmod(0o600)
            break

        if store.strip().lower() in ("n", ""):
            break


def get_oauth_access_token(client_id, client_secret, token_url, scope) -> str:
    payload = [('grant_type', 'client_credentials')]
    scopes = scope.split()
    for item in scopes:
        payload.append(('scope', item))

    data = urlencode(payload)
    auth = HTTPBasicAuth(client_id, client_secret)
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }

    response = requests.post(token_url, data=data, headers=headers, auth=auth)

    if response.status_code == 200:
        token_data = response.json()
        access_token = token_data.get('access_token', '')
        return access_token
    else:
        raise Exception(f"Failed to obtain access token. Status code: {response.status_code}, text: {response.text}")
