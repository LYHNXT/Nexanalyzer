"""Sign-in and upload to OneNote through Microsoft Graph.

**Sign-in is MSAL's device-code flow.** Streamlit runs in a browser tab the
server cannot redirect, so the interactive auth-code flow has nowhere to land;
the device code needs only a URL and a short code the operator types in. Each
operator signs in as themselves, so a page shows who published to it.

**The token cache lives in the user's profile, never in the repo.** It holds a
refresh token. `%LOCALAPPDATA%/NexAnalyzer/onenote_token_cache.bin`, so a
sign-in survives an app restart but not a different Windows user.

**Where pages go:** one notebook (`data/onenote.json`), one section per tool,
one page per wafer, titled with the wafer ID. The first upload creates the
page; every later one appends a dated section to it. The notebook and section
are created on first use. With `site` set, the notebook is a SharePoint site's
(a team notebook); with it empty, the operator's own.

**Lookups list and match in Python** rather than filter in OData, so a wafer
or tool name never has to survive OData string quoting.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.paths import DATA_DIR
from modules.onenote.processing import page_html

GRAPH = "https://graph.microsoft.com/v1.0"
#: `.All` because a team notebook belongs to a SharePoint site, not the user.
SCOPES = ["Notes.ReadWrite.All"]

CONFIG_PATH = DATA_DIR / "onenote.json"


class GraphError(Exception):
    """A Graph call that did not succeed, with Graph's own message."""


@dataclass(frozen=True)
class OneNoteConfig:
    client_id: str = ""
    tenant_id: str = ""
    notebook: str = "Wafer QC"
    #: "<host>:/sites/<path>" for a team notebook, or "" for the user's own.
    site: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.tenant_id and self.notebook)


def load_config(path: Path = CONFIG_PATH) -> OneNoteConfig:
    """Read `data/onenote.json`; a missing or unreadable file is unconfigured."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return OneNoteConfig()
    if not isinstance(raw, dict):
        return OneNoteConfig()
    fields = OneNoteConfig.__dataclass_fields__
    return OneNoteConfig(**{k: str(v or "").strip() for k, v in raw.items() if k in fields})


def default_cache_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "NexAnalyzer" / "onenote_token_cache.bin"


class Auth:
    """MSAL public client with a file-backed token cache."""

    def __init__(self, config: OneNoteConfig, cache_path: Optional[Path] = None):
        import msal

        self._cache_path = Path(cache_path or default_cache_path())
        self._cache = msal.SerializableTokenCache()
        if self._cache_path.exists():
            try:
                self._cache.deserialize(self._cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        self._app = msal.PublicClientApplication(
            config.client_id,
            authority=f"https://login.microsoftonline.com/{config.tenant_id}",
            token_cache=self._cache,
        )

    def _save(self) -> None:
        if self._cache.has_state_changed:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(self._cache.serialize(), encoding="utf-8")

    def account_name(self) -> Optional[str]:
        accounts = self._app.get_accounts()
        return accounts[0].get("username") if accounts else None

    def token_silent(self) -> Optional[str]:
        """A token from the cache, refreshed if need be; None if sign-in is needed."""
        accounts = self._app.get_accounts()
        if not accounts:
            return None
        result = self._app.acquire_token_silent(SCOPES, account=accounts[0])
        self._save()
        return (result or {}).get("access_token")

    def start_device_flow(self) -> dict:
        flow = self._app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise GraphError(flow.get("error_description") or "Could not start sign-in.")
        return flow

    def finish_device_flow(self, flow: dict) -> str:
        """Blocks until the operator has entered the code, or the code expires."""
        result = self._app.acquire_token_by_device_flow(flow)
        self._save()
        if "access_token" not in result:
            raise GraphError(result.get("error_description") or "Sign-in failed.")
        return result["access_token"]

    def sign_out(self) -> None:
        for account in self._app.get_accounts():
            self._app.remove_account(account)
        self._save()


class OneNoteClient:
    """The handful of OneNote calls publishing needs."""

    def __init__(self, token: str, site: str = "", session=None):
        if session is None:
            import requests

            session = requests.Session()
        self._session = session
        self._session.headers["Authorization"] = f"Bearer {token}"
        self._site = site
        self._base: Optional[str] = None

    # -- plumbing ------------------------------------------------------------

    def _call(self, method: str, url: str, **kwargs):
        response = self._session.request(method, url, timeout=60, **kwargs)
        if response.status_code >= 400:
            try:
                message = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = response.text[:300]
            raise GraphError(f"{method} {url.split('?')[0]} → {response.status_code}: {message}")
        return response

    def _list(self, url: str) -> list[dict]:
        items: list[dict] = []
        while url:
            body = self._call("GET", url).json()
            items.extend(body.get("value", []))
            url = body.get("@odata.nextLink")
        return items

    @property
    def base(self) -> str:
        if self._base is None:
            if self._site:
                site_id = self._call("GET", f"{GRAPH}/sites/{self._site}").json()["id"]
                self._base = f"{GRAPH}/sites/{site_id}/onenote"
            else:
                self._base = f"{GRAPH}/me/onenote"
        return self._base

    # -- notebook / section / page --------------------------------------------

    def notebook_id(self, name: str) -> str:
        for notebook in self._list(f"{self.base}/notebooks?$select=id,displayName"):
            if notebook.get("displayName") == name:
                return notebook["id"]
        return self._call("POST", f"{self.base}/notebooks", json={"displayName": name}).json()["id"]

    def section_id(self, notebook_id: str, name: str) -> str:
        url = f"{self.base}/notebooks/{notebook_id}/sections"
        for section in self._list(f"{url}?$select=id,displayName"):
            if section.get("displayName") == name:
                return section["id"]
        return self._call("POST", url, json={"displayName": name}).json()["id"]

    def find_page(self, section_id: str, title: str) -> Optional[str]:
        url = f"{self.base}/sections/{section_id}/pages?$select=id,title&$top=100"
        for page in self._list(url):
            if page.get("title") == title:
                return page["id"]
        return None

    def create_page(self, section_id: str, document: str, parts) -> None:
        files = {"Presentation": (None, document.encode("utf-8"), "text/html")}
        files.update({p.name: (p.name, p.data, p.content_type) for p in parts})
        self._call("POST", f"{self.base}/sections/{section_id}/pages", files=files)

    def append_to_page(self, page_id: str, commands: str, parts) -> None:
        files = {"Commands": (None, commands.encode("utf-8"), "application/json")}
        files.update({p.name: (p.name, p.data, p.content_type) for p in parts})
        self._call("PATCH", f"{self.base}/pages/{page_id}/content", files=files)


def publish_section(
    client: OneNoteClient,
    notebook: str,
    section: str,
    wafer_id: str,
    body: str,
    parts,
) -> str:
    """Append one section to the wafer's page, creating the page if absent.

    Returns "created" or "appended".
    """
    section_id = client.section_id(client.notebook_id(notebook), section)
    page_id = client.find_page(section_id, wafer_id)
    if page_id is None:
        client.create_page(section_id, page_html.new_page_html(wafer_id, body), parts)
        return "created"
    client.append_to_page(page_id, page_html.append_commands(body), parts)
    return "appended"
