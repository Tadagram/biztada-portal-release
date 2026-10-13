#!/usr/bin/env python3
"""
Tadagram Worker Portal Linux - Auto-Updater Daemon
===================================================
Continuously checks the official Telegram Release Channel (@tadabiz_release_manager)
and svc-core-api for newer Linux worker binaries (.wrkx), downloads updates,
verifies cryptographic integrity, and applies atomic updates to active workers.

Equivalent in functionality and protocol to the Tauri Worker Portal Update Subsystem on Windows.
"""

import os
import sys
import time
import json
import re
import logging
import asyncio
import hashlib
import tempfile
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from packaging import version as semver_parser
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [Updater] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("worker_updater")

# Configuration Defaults
API_URL = os.getenv("CORE_API_URL", "https://core.tadagram.com").rstrip("/")
API_TOKEN = os.getenv("CORE_API_TOKEN", "")
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_RELEASE_CHANNEL = os.getenv("TELEGRAM_RELEASE_CHANNEL", "tadabiz_release_manager")
WORKERS_DIR = Path(os.getenv("WORKERS_DATA_DIR", "/app/data/workers")).resolve()
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SECONDS", "1800")) # 30 mins
ENCRYPTION_KEY = os.getenv("WRKX_ENCRYPTION_KEY", "")

# Regex for detecting worker releases and platform tagging
# Matches:
#   wkr-marketing-linux_v1.2.3.wrkx
#   wkr-marketing_linux_v1.2.3.wrkx
#   wkr-marketing_v1.2.3-linux.wrkx
#   wkr-marketing_v1.2.3.wrkx
RELEASE_RE = re.compile(r"^([a-z0-9-_]+?)[_-]v?(\d+\.\d+(?:\.\d+)?(?:-[a-z0-9.]+)?)\.(wrkx|exe|bin|tar\.gz)$", re.IGNORECASE)


def parse_release_filename(filename: str) -> Optional[Tuple[str, str, str]]:
    """
    Parses worker release filename into (normalized_worker_id, semver, platform).
    Platform is 'linux', 'windows', or 'all'.
    """
    m = RELEASE_RE.match(filename)
    if not m:
        return None

    raw_id, ver, ext = m.groups()
    platform = "all"
    norm_id = raw_id.lower()

    # Detect platform tag in worker_id
    if norm_id.endswith("-linux") or norm_id.endswith("_linux"):
        platform = "linux"
        norm_id = re.sub(r"[-_]linux$", "", norm_id)
    elif norm_id.endswith("-windows") or norm_id.endswith("_windows") or norm_id.endswith("-win") or norm_id.endswith("_win"):
        platform = "windows"
        norm_id = re.sub(r"[-_](?:windows|win)$", "", norm_id)

    # Detect platform tag in version suffix (e.g. 1.2.3-linux)
    ver_lower = ver.lower()
    if ver_lower.endswith("-linux") or ver_lower.endswith("_linux"):
        platform = "linux"
        ver = re.sub(r"[-_]linux$", "", ver, flags=re.IGNORECASE)
    elif ver_lower.endswith("-windows") or ver_lower.endswith("_windows") or ver_lower.endswith("-win") or ver_lower.endswith("_win"):
        platform = "windows"
        ver = re.sub(r"[-_](?:windows|win)$", "", ver, flags=re.IGNORECASE)
    elif "linux" in filename.lower():
        platform = "linux"
    elif "windows" in filename.lower() or "win" in filename.lower():
        platform = "windows"

    return norm_id, ver, platform


def get_installed_workers() -> Dict[str, str]:
    """
    Scans the local workers directory and retrieves the installed version
    for each worker.
    """
    installed = {}
    if not WORKERS_DIR.exists():
        WORKERS_DIR.mkdir(parents=True, exist_ok=True)
        return installed

    for item in WORKERS_DIR.iterdir():
        if item.is_dir():
            worker_id = item.name
            worker_json = item / "worker.json"
            version_txt = item / "version.txt"

            ver = None
            if worker_json.exists():
                try:
                    data = json.loads(worker_json.read_text(encoding="utf-8"))
                    ver = data.get("version")
                except Exception as e:
                    logger.warning(f"Error reading {worker_json}: {e}")

            if not ver and version_txt.exists():
                try:
                    ver = version_txt.read_text(encoding="utf-8").strip()
                except Exception as e:
                    logger.warning(f"Error reading {version_txt}: {e}")

            if ver:
                installed[worker_id] = ver

    return installed


def fetch_releases_from_api() -> List[dict]:
    """
    Fetches available worker releases from Core API.
    """
    url = f"{API_URL}/api/updates/worker-releases"
    headers = {}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and "data" in data:
                return data["data"]
            elif isinstance(data, list):
                return data
        else:
            logger.warning(f"API /api/updates/worker-releases returned status {resp.status_code}")
    except Exception as e:
        logger.warning(f"Failed to fetch releases from API: {e}")

    return []


async def fetch_releases_from_telegram() -> List[dict]:
    """
    Direct MTProto scanner from Telegram Channel when credentials are provided.
    """
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        return []

    from telethon import TelegramClient
    from telethon.tl.types import DocumentAttributeFilename

    releases = []
    session_file = Path("/tmp/tadagram_updater_tg.session")
    client = TelegramClient(str(session_file), TELEGRAM_API_ID, TELEGRAM_API_HASH)

    try:
        await client.connect()
        if not await client.is_user_authorized():
            logger.info("Telegram MTProto client not authorized for direct scan, falling back to API.")
            return []

        entity = await client.get_entity(TELEGRAM_RELEASE_CHANNEL)
        messages = await client.get_messages(entity, limit=50)

        for msg in messages:
            if msg.media and hasattr(msg.media, "document") and msg.media.document:
                doc = msg.media.document
                fname = ""
                for attr in doc.attributes:
                    if isinstance(attr, DocumentAttributeFilename):
                        fname = attr.file_name
                        break

                parsed = parse_release_filename(fname)
                if parsed:
                    worker_id, ver, platform = parsed
                    releases.append({
                        "worker_id": worker_id,
                        "version": ver,
                        "filename": fname,
                        "size_bytes": doc.size,
                        "posted_at": msg.date.isoformat() if msg.date else "",
                        "message_id": msg.id,
                        "channel_username": TELEGRAM_RELEASE_CHANNEL,
                        "platform": platform,
                        "changelog": (msg.text or "").strip()
                    })
    except Exception as e:
        logger.error(f"Telegram MTProto scan error: {e}")
    finally:
        await client.disconnect()

    return releases


def filter_linux_updates(all_releases: List[dict], installed: Dict[str, str]) -> List[dict]:
    """
    Filters releases that target Linux and are strictly newer than local installed version.
    """
    updates = []
    # Deduplicate by worker_id, keeping highest version
    latest_by_worker = {}

    for r in all_releases:
        # Platform check: allow "linux" and "all"
        platform = (r.get("platform") or "all").lower()
        if platform not in ("linux", "all"):
            continue

        worker_id = r["worker_id"]
        ver = r["version"]

        prev = latest_by_worker.get(worker_id)
        if not prev or semver_parser.parse(ver) > semver_parser.parse(prev["version"]):
            latest_by_worker[worker_id] = r

    for worker_id, r in latest_by_worker.items():
        curr_ver = installed.get(worker_id)
        if not curr_ver:
            # Not installed yet
            logger.info(f"Worker '{worker_id}' is available for initial installation (v{r['version']}).")
            updates.append(r)
        else:
            try:
                if semver_parser.parse(r["version"]) > semver_parser.parse(curr_ver):
                    logger.info(f"Update found for '{worker_id}': v{curr_ver} -> v{r['version']}")
                    updates.append(r)
                else:
                    logger.debug(f"Worker '{worker_id}' is up to date (v{curr_ver}).")
            except Exception as e:
                logger.warning(f"Error comparing version {r['version']} and {curr_ver}: {e}")

    return updates


def download_and_install_worker(release: dict) -> bool:
    """
    Downloads worker package and performs atomic deployment.
    """
    worker_id = release["worker_id"]
    version = release["version"]
    filename = release["filename"]
    target_dir = WORKERS_DIR / worker_id
    target_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading update for '{worker_id}' ({filename})...")
    download_url = f"{API_URL}/api/updates/download/{filename}"
    headers = {}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_file = Path(tmpdir) / filename
        try:
            with requests.get(download_url, headers=headers, stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(tmp_file, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        f.write(chunk)
            logger.info(f"Downloaded {filename} ({tmp_file.stat().st_size} bytes).")
        except Exception as e:
            logger.error(f"Failed to download {filename}: {e}")
            return False

        # Verify SHA256 if provided
        if release.get("sha256"):
            hasher = hashlib.sha256()
            with open(tmp_file, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            calculated_hash = hasher.hexdigest()
            if calculated_hash.lower() != release["sha256"].lower():
                logger.error(f"SHA256 mismatch for {filename}! Expected {release['sha256']}, got {calculated_hash}")
                return False

        # Atomic installation
        dest_wrkx = target_dir / f"{worker_id}.wrkx"
        dest_version = target_dir / "version.txt"
        dest_json = target_dir / "worker.json"

        # Backup existing
        if dest_wrkx.exists():
            shutil.copy2(dest_wrkx, target_dir / f"{worker_id}.wrkx.bak")

        try:
            shutil.copy2(tmp_file, dest_wrkx)
            dest_version.write_text(version, encoding="utf-8")
            
            # Update worker.json metadata
            cfg_data = {
                "worker_id": worker_id,
                "version": version,
                "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "filename": filename,
                "platform": "linux"
            }
            dest_json.write_text(json.dumps(cfg_data, indent=2), encoding="utf-8")
            logger.info(f"Successfully installed '{worker_id}' v{version} into {target_dir}")

            # Notify supervisor / restart container
            restart_worker_service(worker_id)
            return True
        except Exception as e:
            logger.error(f"Failed during file copy for {worker_id}: {e}")
            if (target_dir / f"{worker_id}.wrkx.bak").exists():
                shutil.copy2(target_dir / f"{worker_id}.wrkx.bak", dest_wrkx)
            return False


def restart_worker_service(worker_id: str):
    """
    Triggers container or supervisor reload for the updated worker.
    """
    logger.info(f"Restarting service for worker: {worker_id}")
    # If running inside docker-compose or supervisor
    try:
        subprocess.run(["docker", "compose", "restart", worker_id], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


async def check_and_update_cycle():
    """
    Performs one check and update pass.
    """
    logger.info("Scanning for Linux worker updates...")
    installed = get_installed_workers()
    logger.info(f"Currently installed workers: {installed}")

    releases = []
    # 1. Try direct Telegram MTProto
    try:
        releases = await fetch_releases_from_telegram()
    except Exception as e:
        logger.debug(f"Telegram scan skipped: {e}")

    # 2. Fallback to Core API
    if not releases:
        releases = fetch_releases_from_api()

    if not releases:
        logger.info("No releases found on Telegram channel or API.")
        return

    updates = filter_linux_updates(releases, installed)
    if not updates:
        logger.info("All Linux workers are currently up to date.")
        return

    for upd in updates:
        download_and_install_worker(upd)


async def main():
    logger.info("Starting Tadagram Worker Portal Linux Auto-Updater Daemon...")
    while True:
        try:
            await check_and_update_cycle()
        except Exception as e:
            logger.error(f"Unexpected error in update cycle: {e}", exc_info=True)

        logger.info(f"Sleeping for {CHECK_INTERVAL} seconds until next update scan...")
        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Auto-Updater terminated by operator.")
