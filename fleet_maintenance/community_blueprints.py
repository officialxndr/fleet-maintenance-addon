import json
import os
import time
import uuid

import requests

# ---------------------------------------------------------------------------
# Configuration — swap these out when migrating to Supabase
# ---------------------------------------------------------------------------
_GITHUB_OWNER = "officialxndr"
_GITHUB_REPO  = "fleet-maintenance-blueprints"
_RAW_BASE     = f"https://raw.githubusercontent.com/{_GITHUB_OWNER}/{_GITHUB_REPO}/main"

COMMUNITY_INDEX_URL  = f"{_RAW_BASE}/index.json"
COMMUNITY_BASE_URL   = f"{_RAW_BASE}/"
COMMUNITY_REPO_URL   = f"https://github.com/{_GITHUB_OWNER}/{_GITHUB_REPO}"
COMMUNITY_SUBMIT_URL = "https://curly-math-736f.zander-halverson99.workers.dev"  # Set to your Cloudflare Worker URL after deployment

CACHE_TTL_SECONDS = 86400  # 24 hours

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_data_dir():
    """Same resolution logic as core.py DB_DIR."""
    if os.path.exists("/config"):
        return "/config"
    if os.path.exists("/data"):
        return "/data"
    data_dir = os.path.join(os.getcwd(), "data")
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def _cache_path():
    return os.path.join(_get_data_dir(), "community_index_cache.json")


def _local_library_path():
    return os.path.join(_get_data_dir(), "blueprint_library.json")


# ---------------------------------------------------------------------------
# Local blueprint library (same-instance sharing)
# ---------------------------------------------------------------------------

def load_local_library():
    path = _local_library_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except (ValueError, OSError):
        return []


def save_local_library(library):
    with open(_local_library_path(), "w") as f:
        json.dump(library, f, indent=2)


def publish_to_local_library(blueprint_data, vin_label=""):
    """Append a blueprint to the local library. Returns the new entry."""
    library = load_local_library()
    services = blueprint_data.get("services", [])
    entry = {
        "id": str(uuid.uuid4())[:8],
        "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "year": blueprint_data.get("year", ""),
        "make": blueprint_data.get("make", ""),
        "model": blueprint_data.get("model", ""),
        "label": " ".join(filter(None, [
            blueprint_data.get("year", ""),
            blueprint_data.get("make", ""),
            blueprint_data.get("model", ""),
        ])),
        "service_count": len(services),
        "has_specs": bool(any(v for v in blueprint_data.get("specs", {}).values())),
        "has_torque": bool(blueprint_data.get("torque_specs")),
        "data": blueprint_data,
    }
    library.append(entry)
    save_local_library(library)
    return entry


def delete_from_local_library(bp_id):
    library = load_local_library()
    updated = [b for b in library if b.get("id") != bp_id]
    save_local_library(updated)
    return len(updated) < len(library)


def search_local_library(make="", model=""):
    results = []
    for entry in load_local_library():
        if make and entry.get("make", "").lower() != make.lower():
            continue
        if model and entry.get("model", "").lower() != model.lower():
            continue
        results.append({k: v for k, v in entry.items() if k != "data"})
    return results


def get_local_blueprint(bp_id):
    for entry in load_local_library():
        if entry.get("id") == bp_id:
            return entry
    return None


# ---------------------------------------------------------------------------
# Community blueprint library (GitHub-backed, 24h cache)
# ---------------------------------------------------------------------------

def _load_cache():
    path = _cache_path()
    if not os.path.exists(path):
        return None, 0
    try:
        with open(path) as f:
            cached = json.load(f)
        return cached.get("index", []), cached.get("fetched_at", 0)
    except (ValueError, OSError):
        return None, 0


def _save_cache(index):
    with open(_cache_path(), "w") as f:
        json.dump({"fetched_at": time.time(), "index": index}, f)


def fetch_community_index(make="", model=""):
    """Return community blueprint metadata, filtered by make/model. Uses 24h cache."""
    index, fetched_at = _load_cache()
    if index is None or (time.time() - fetched_at) > CACHE_TTL_SECONDS:
        try:
            resp = requests.get(COMMUNITY_INDEX_URL, timeout=5)
            resp.raise_for_status()
            index = resp.json()
            _save_cache(index)
        except Exception:
            index = index or []

    results = []
    for entry in index:
        if make and entry.get("make", "").lower() != make.lower():
            continue
        if model and entry.get("model", "").lower() != model.lower():
            continue
        results.append(entry)
    return results


def fetch_community_blueprint(bp_id):
    """Fetch full blueprint data from the community repo by ID."""
    index, _ = _load_cache()
    if not index:
        index = fetch_community_index()
    entry = next((e for e in index if e.get("id") == bp_id), None)
    if not entry:
        return None
    file_path = entry.get("file", f"blueprints/{bp_id}.json")
    try:
        resp = requests.get(f"{COMMUNITY_BASE_URL}{file_path}", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def invalidate_cache():
    path = _cache_path()
    if os.path.exists(path):
        os.remove(path)


def get_repo_url():
    return COMMUNITY_REPO_URL


def submit_blueprint(blueprint_data):
    """
    POST blueprint to the Cloudflare Worker submission endpoint.
    Returns {"status": "submitted", "issue_url": "..."} on success,
    or {"status": "error", "message": "..."} on failure.
    If COMMUNITY_SUBMIT_URL is not configured, returns a config_needed error.
    """
    if os.environ.get('DEMO_MODE', '').lower() in ('1', 'true', 'yes'):
        return {"status": "error", "message": "Blueprint submission is disabled in the demo."}
    if not COMMUNITY_SUBMIT_URL:
        return {"status": "error", "message": "Submission endpoint not configured."}
    try:
        resp = requests.post(
            COMMUNITY_SUBMIT_URL,
            json=blueprint_data,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("status") == "submitted":
            return {"status": "submitted", "issue_url": data.get("issue_url", "")}
        return {"status": "error", "message": data.get("error", "Submission failed.")}
    except requests.Timeout:
        return {"status": "error", "message": "Request timed out. Please try again."}
    except Exception as e:
        return {"status": "error", "message": str(e)}
