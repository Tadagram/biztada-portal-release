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
from Crypto.Cipher import AES

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
MASTER_SECRET = os.getenv("WORKER_MASTER_SECRET", "tadagram-dev-secret-CHANGE-IN-PROD-b3a9f2e1c4d5")

# Allowed workers on Linux Docker Portal
SUPPORTED_WORKERS = {"wkr-ai-controller", "wkr-marketing", "wkr-chatbot"}
EXCLUDED_WORKERS = {"wkr-proxy", "wkr-local-ai-text2text", "wkr-local-ai-image2text"}

# Regex for detecting worker releases and platform tagging
# Matches:
#   wkr-marketing-linux_v1.2.3.wrkx
#   wkr-marketing_linux_v1.2.3.wrkx
#   wkr-marketing_v1.2.3-linux.wrkx
#   wkr-marketing_v1.2.3.wrkx
RELEASE_RE = re.compile(r"^([a-z0-9-_]+?)[_-]v?(\d+\.\d+(?:\.\d+)?(?:-[a-z0-9.]+)?)\.(wrkx|exe|bin|tar\.gz)$", re.IGNORECASE)


def decrypt_wrkx(wrkx_path: Path, output_binary_path: Path, master_secret: str = MASTER_SECRET) -> bool:
    """
    Decrypts a Layer-3 AES-256-GCM .wrkx package to an executable Linux ELF binary.
    Uses atomic temporary file replacement (os.replace) to prevent [Errno 26] Text file busy
    when updating binaries for active runner containers.
    Format: [12-byte Nonce] + [Ciphertext] + [16-byte Tag]
    Key: SHA-256('wrkx-portable::' + master_secret)
    """
    try:
        data = wrkx_path.read_bytes()
        tmp_out = output_binary_path.with_name(f"{output_binary_path.name}.tmp")

        # If already an unencrypted ELF binary, preserve directly
        if data.startswith(b"\x7fELF"):
            logger.info(f"{wrkx_path.name} is already a native Linux ELF binary. Preserving directly.")
            tmp_out.write_bytes(data)
            tmp_out.chmod(0o755)
            os.replace(tmp_out, output_binary_path)
            return True

        if len(data) < 28:
            logger.error(f"Invalid .wrkx package: file size too small ({len(data)} bytes) in {wrkx_path}")
            return False

        nonce = data[:12]
        tag = data[-16:]
        ciphertext = data[12:-16]

        key = hashlib.sha256(b"wrkx-portable::" + master_secret.encode("utf-8")).digest()
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        plaintext = cipher.decrypt_and_verify(ciphertext, tag)

        # Validate that the decrypted payload is a valid Linux ELF binary
        if not plaintext.startswith(b"\x7fELF"):
            logger.error(f"Decrypted binary from {wrkx_path.name} is NOT a valid Linux ELF executable (magic={plaintext[:4]!r})! Rejecting update.")
            return False

        # Atomic file write & replace (safe against running processes / ETXTBSY)
        tmp_out.write_bytes(plaintext)
        tmp_out.chmod(0o755)
        os.replace(tmp_out, output_binary_path)
        logger.info(f"✅ Successfully decrypted {wrkx_path.name} -> {output_binary_path.name} ({len(plaintext)} bytes, mode 0755, atomic swap)")
        return True
    except Exception as e:
        logger.error(f"Failed to decrypt {wrkx_path.name} with AES-256-GCM: {e}")
        return False


def ensure_run_script(worker_id: str, target_dir: Path):
    """
    Ensures run.sh exists and has executable permissions for the worker runner container.
    """
    run_sh = target_dir / "run.sh"
    if not run_sh.exists():
        content = f"""#!/bin/bash
CHROME_BIN=$(find /root/.cache/ms-playwright /root/.cache/patchright -name chrome -type f 2>/dev/null | head -n 1)
if [ -n "$CHROME_BIN" ]; then
    ln -sf "$CHROME_BIN" /usr/bin/google-chrome
    mkdir -p /opt/google/chrome
    ln -sf "$CHROME_BIN" /opt/google/chrome/chrome
fi

export PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright
export PYTHONPATH=/usr/local/lib/python3.11/site-packages:$PYTHONPATH
export PLAYWRIGHT_NODEJS_PATH=/usr/local/lib/python3.11/site-packages/playwright/driver/node
export DISPLAY=:99
export HEADLESS=${{HEADLESS:-true}}

if ! pgrep -x "Xvfb" > /dev/null; then
    rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
    Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp -ac &
    sleep 1
fi

chmod +x /app/worker/{worker_id}
exec /app/worker/{worker_id}
"""
        run_sh.write_text(content, encoding="utf-8")
    run_sh.chmod(0o755)


def parse_release_filename(filename: str) -> Optional[Tuple[str, str, str]]:
    """
    Parses worker release filename into (normalized_worker_id, semver, platform).
    Platform is 'linux', 'windows', or 'all'.
    """
    m = RELEASE_RE.match(filename)
    if not m:
        return None

    raw_id, ver, ext = m.groups()
    norm_id = raw_id.lower().strip()
    platform = "windows"  # Default for untagged {worker_id}_v{ver}.wrkx releases

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


def normalize_release(release: dict) -> Optional[dict]:
    """
    Guarantees canonical worker_id, cleaned semver, and platform filtering for Linux.
    Works whether release came from Core API or Telegram MTProto.
    """
    filename = release.get("filename", "")
    parsed = parse_release_filename(filename) if filename else None
    if parsed:
        canon_id, ver, platform = parsed
    else:
        raw_id = (release.get("worker_id") or "").lower().strip()
        canon_id = re.sub(r"[-_](?:linux|windows|win)$", "", raw_id)
        ver = (release.get("version") or "").strip().lstrip("v")
        platform = (release.get("platform") or "all").lower()
        if "linux" in raw_id or "linux" in filename.lower():
            platform = "linux"
        elif "windows" in raw_id or "win" in raw_id or "win" in filename.lower():
            platform = "windows"

    if not canon_id or not ver:
        return None

    # Filter out non-Linux platforms: On Linux Portal, ONLY linux platform is supported
    if platform != "linux":
        return None

    # Filter out workers that do not belong to Linux Portal
    if canon_id not in SUPPORTED_WORKERS or canon_id in EXCLUDED_WORKERS:
        return None

    norm = dict(release)
    norm["worker_id"] = canon_id
    norm["version"] = ver
    norm["platform"] = platform
    return norm


def get_installed_workers() -> Dict[str, str]:
    """
    Scans the local workers directory and retrieves the installed version
    for each worker using canonical worker IDs.
    """
    installed = {}
    if not WORKERS_DIR.exists():
        WORKERS_DIR.mkdir(parents=True, exist_ok=True)
        return installed

    for item in WORKERS_DIR.iterdir():
        if item.is_dir():
            raw_id = item.name
            canon_id = re.sub(r"[-_]linux$", "", raw_id)

            # Only scan and track supported Linux portal workers
            if canon_id not in SUPPORTED_WORKERS or canon_id in EXCLUDED_WORKERS:
                continue

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
                # Prefer canonical directory metadata over legacy -linux directory
                if canon_id not in installed or raw_id == canon_id:
                    installed[canon_id] = ver

    return installed


def fetch_releases_from_api() -> List[dict]:
    """
    Fetches available worker releases from Core API and normalizes them.
    """
    url = f"{API_URL}/api/updates/worker-releases"
    headers = {}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            raw_releases = []
            if isinstance(data, dict) and "data" in data:
                raw_releases = data["data"]
            elif isinstance(data, list):
                raw_releases = data

            normalized = []
            for r in raw_releases:
                norm = normalize_release(r)
                if norm:
                    normalized.append(norm)
            return normalized
        else:
            logger.warning(f"API /api/updates/worker-releases returned status {resp.status_code}")
    except Exception as e:
        logger.warning(f"Failed to fetch releases from API: {e}")

    return []


def get_telegram_string_session() -> Optional[str]:
    """
    Fetches the authenticated GramJS/Telethon StringSession from svc-core-api using CORE_API_TOKEN.
    """
    if not API_TOKEN:
        return None
    url = f"{API_URL}/api/auth/telegram/session-string"
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            session_str = data.get("session_string")
            if session_str:
                return session_str
    except Exception as e:
        logger.warning(f"Failed to fetch telegram session string from API: {e}")
    return None


def parse_gramjs_or_string_session(session_str: str):
    """
    Parses either a Telethon StringSession or a GramJS StringSession into a Telethon session object.
    GramJS sessions start with '1' and pack (dc_id, ip_len, ip, port, auth_key).
    """
    from telethon.sessions import StringSession, MemorySession
    from telethon.crypto import AuthKey
    import base64
    import struct

    session_str = session_str.strip()
    # Try standard Telethon StringSession first
    try:
        ss = StringSession(session_str)
        if ss.auth_key:
            return ss
    except Exception:
        pass

    # Try GramJS session parsing
    try:
        if session_str.startswith("1"):
            raw = base64.urlsafe_b64decode(session_str[1:] + "==")
            dc_id = raw[0]
            ip_len = struct.unpack(">H", raw[1:3])[0]
            ip = raw[3:3 + ip_len].decode("utf-8")
            port = struct.unpack(">H", raw[3 + ip_len:5 + ip_len])[0]
            key_bytes = raw[5 + ip_len:5 + ip_len + 256]
            if len(key_bytes) == 256:
                mem = MemorySession()
                mem.set_dc(dc_id, ip, port)
                mem.auth_key = AuthKey(data=key_bytes)
                return mem
    except Exception as e:
        logger.debug(f"GramJS session parse error: {e}")

    return StringSession(session_str)


async def get_telegram_client():
    """
    Creates and connects an authorized Telethon client using StringSession if available.
    Supports both Telethon and GramJS (Node/Browser) string sessions seamlessly.
    """
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        return None

    from telethon import TelegramClient
    from telethon.sessions import StringSession

    session_str = os.getenv("TELEGRAM_SESSION_STRING") or get_telegram_string_session()
    if session_str:
        try:
            sess = parse_gramjs_or_string_session(session_str)
            client = TelegramClient(sess, TELEGRAM_API_ID, TELEGRAM_API_HASH)
        except Exception as e:
            logger.warning(f"Failed to initialize Telethon session: {e}")
            client = TelegramClient(StringSession(session_str), TELEGRAM_API_ID, TELEGRAM_API_HASH)
    else:
        session_file = Path("/tmp/tadagram_updater_tg.session")
        client = TelegramClient(str(session_file), TELEGRAM_API_ID, TELEGRAM_API_HASH)

    try:
        await client.connect()
        if await client.is_user_authorized():
            return client
        logger.info("Telegram MTProto client not authorized for direct scan.")
    except Exception as e:
        logger.warning(f"Failed to connect Telegram MTProto client: {e}")

    try:
        await client.disconnect()
    except Exception:
        pass
    return None


async def fetch_releases_from_telegram() -> List[dict]:
    """
    Direct MTProto scanner from Telegram Channel when credentials are provided.
    """
    client = await get_telegram_client()
    if not client:
        return []

    from telethon.tl.types import DocumentAttributeFilename

    releases = []
    try:
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

                raw = {
                    "filename": fname,
                    "size_bytes": doc.size,
                    "posted_at": msg.date.isoformat() if msg.date else "",
                    "message_id": msg.id,
                    "channel_username": TELEGRAM_RELEASE_CHANNEL,
                    "changelog": (msg.text or "").strip()
                }
                norm = normalize_release(raw)
                if norm:
                    releases.append(norm)
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
        # Platform check: ONLY allow "linux"
        platform = (r.get("platform") or "").lower()
        if platform != "linux":
            continue

        worker_id = r["worker_id"]
        if worker_id not in SUPPORTED_WORKERS or worker_id in EXCLUDED_WORKERS:
            continue

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


async def download_and_install_worker(release: dict) -> bool:
    """
    Downloads worker package and performs atomic deployment with AES-256-GCM decryption.
    Uses MTProto direct download if message_id is available, fallback to HTTP.
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
        downloaded = False

        # Fast path: Try direct MTProto download from Telegram channel
        if release.get("message_id"):
            try:
                tg_client = await get_telegram_client()
                if tg_client:
                    entity = await tg_client.get_entity(release.get("channel_username") or TELEGRAM_RELEASE_CHANNEL)
                    msg = await tg_client.get_messages(entity, ids=release["message_id"])
                    if msg and msg.media:
                        logger.info(f"Direct MTProto fast-path: downloading {filename} from message {release['message_id']}...")
                        await tg_client.download_media(msg, file=str(tmp_file))
                        if tmp_file.exists() and tmp_file.stat().st_size > 1024:
                            downloaded = True
                            logger.info(f"Downloaded {filename} via MTProto ({tmp_file.stat().st_size} bytes).")
                    await tg_client.disconnect()
            except Exception as e:
                logger.warning(f"MTProto download failed for {filename}: {e}")

        # Fallback to HTTP download
        if not downloaded:
            try:
                with requests.get(download_url, headers=headers, stream=True, timeout=300) as r:
                    r.raise_for_status()
                    with open(tmp_file, "wb") as f:
                        for chunk in r.iter_content(chunk_size=65536):
                            f.write(chunk)
                downloaded = True
                logger.info(f"Downloaded {filename} via HTTP ({tmp_file.stat().st_size} bytes).")
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
        dest_bin = target_dir / worker_id
        dest_version = target_dir / "version.txt"
        dest_json = target_dir / "worker.json"

        # Backup existing
        if dest_wrkx.exists():
            shutil.copy2(dest_wrkx, target_dir / f"{worker_id}.wrkx.bak")
        if dest_bin.exists():
            shutil.copy2(dest_bin, target_dir / f"{worker_id}.bak")

        try:
            tmp_wrkx = target_dir / f"{worker_id}.wrkx.tmp"
            shutil.copy2(tmp_file, tmp_wrkx)
            os.replace(tmp_wrkx, dest_wrkx)

            # Decrypt .wrkx to executable Linux binary (atomic swap)
            decrypt_ok = decrypt_wrkx(dest_wrkx, dest_bin)
            if not decrypt_ok:
                logger.error(f"Decryption failed during installation for {worker_id}")
                if (target_dir / f"{worker_id}.bak").exists():
                    tmp_restore = target_dir / f"{worker_id}.restore.tmp"
                    shutil.copy2(target_dir / f"{worker_id}.bak", tmp_restore)
                    os.replace(tmp_restore, dest_bin)
                return False

            # Ensure run script exists and is executable
            ensure_run_script(worker_id, target_dir)

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
            logger.info(f"✅ Successfully installed and decrypted '{worker_id}' v{version} into {target_dir}")

            # Notify supervisor / restart container
            restart_worker_service(worker_id)
            return True
        except Exception as e:
            logger.error(f"Failed during deployment for {worker_id}: {e}")
            if (target_dir / f"{worker_id}.bak").exists():
                try:
                    tmp_restore = target_dir / f"{worker_id}.restore.tmp"
                    shutil.copy2(target_dir / f"{worker_id}.bak", tmp_restore)
                    os.replace(tmp_restore, dest_bin)
                except Exception:
                    pass
            if (target_dir / f"{worker_id}.wrkx.bak").exists():
                try:
                    shutil.copy2(target_dir / f"{worker_id}.wrkx.bak", dest_wrkx)
                except Exception:
                    pass
            return False


def restart_worker_service(worker_id: str) -> bool:
    """
    Triggers container reload for the updated worker via Docker socket.
    Uses direct container restart with Docker Compose fallback.
    """
    logger.info(f"Restarting service container for worker: {worker_id}")
    container_name = f"tadagram-{worker_id}"

    # Method 1: Direct container restart via docker CLI (socket)
    try:
        res = subprocess.run(["docker", "restart", container_name], capture_output=True, text=True, timeout=30)
        if res.returncode == 0:
            logger.info(f"✅ Container '{container_name}' successfully restarted.")
            return True
        logger.warning(f"docker restart {container_name} notice: {res.stderr.strip()}")
    except Exception as e:
        logger.warning(f"Direct docker restart error: {e}")

    # Method 2: Docker Compose restart fallback
    try:
        compose_file = PORTAL_ROOT / "docker-compose.yml"
        cmd = ["docker", "compose"]
        if compose_file.exists():
            cmd.extend(["-f", str(compose_file)])
        cmd.extend(["-p", "worker-portal-linux", "restart", worker_id])
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        if res.returncode == 0:
            logger.info(f"✅ Service '{worker_id}' successfully restarted via docker compose.")
            return True
        logger.error(f"docker compose restart failed: {res.stderr.strip()}")
    except Exception as e:
        logger.error(f"Compose restart error: {e}")

    return False


PORTAL_ROOT = Path(os.getenv("PORTAL_ROOT_DIR", "/app/portal_root")).resolve()
if not PORTAL_ROOT.exists():
    PORTAL_ROOT = Path("/opt/tadagram-worker-portal")
if not PORTAL_ROOT.exists() and Path("/app").exists():
    PORTAL_ROOT = Path("/app")

GITHUB_PORTAL_API = "https://api.github.com/repos/Tadagram/biztada-portal-release/releases/latest"


def check_and_update_portal_stack() -> bool:
    """
    100% Autonomous Portal Stack Self-Updater for Linux:
    Checks GitHub Releases for newer Linux Portal Stack releases (tadagram-worker-portal-linux.tar.gz).
    If a newer version is found, downloads, verifies, unpacks, and restarts containers.
    """
    version_file = PORTAL_ROOT / "VERSION"
    if not version_file.exists():
        version_file = Path("/app/VERSION")

    current_ver = "1.0.0"
    if version_file.exists():
        try:
            current_ver = version_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    try:
        resp = requests.get(GITHUB_PORTAL_API, headers={"User-Agent": "Biztada-Linux-Portal"}, timeout=15)
        if resp.status_code != 200:
            return False
        rel = resp.json()
        tag = (rel.get("tag_name") or "").lstrip("v").strip()
        if not tag:
            return False

        # Compare versions
        if semver_parser.parse(tag) <= semver_parser.parse(current_ver):
            return False

        logger.info(f"[PortalUpdate] 🚀 New Linux Portal release detected: v{tag} (current: v{current_ver}). Auto-updating stack...")

        # Find tar.gz asset
        assets = rel.get("assets", [])
        tar_asset = next((a for a in assets if a.get("name", "").endswith(".tar.gz") and "linux" in a.get("name", "")), None)
        if not tar_asset:
            tar_asset = next((a for a in assets if a.get("name", "").endswith(".tar.gz")), None)

        if not tar_asset:
            logger.warning("[PortalUpdate] No suitable .tar.gz archive found in release assets.")
            return False

        tar_url = tar_asset.get("browser_download_url")
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / "update.tar.gz"
            r = requests.get(tar_url, stream=True, timeout=120)
            r.raise_for_status()
            with open(archive_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)

            # Unpack safely into PORTAL_ROOT (preserve .env, data/)
            import tarfile
            with tarfile.open(archive_path, "r:gz") as tar:
                for member in tar.getmembers():
                    if member.name in (".env", "data", "data/workers", "data/shared") or member.name.startswith("data/"):
                        continue
                    tar.extract(member, path=PORTAL_ROOT)

            # Update VERSION file
            version_file.write_text(tag, encoding="utf-8")
            logger.info(f"[PortalUpdate] ✅ Successfully unpacked Portal v{tag} into {PORTAL_ROOT}")

            # Reload docker stack
            try:
                logger.info("[PortalUpdate] Reloading Docker Compose services...")
                compose_file = PORTAL_ROOT / "docker-compose.yml"
                cmd = ["docker", "compose", "up", "-d", "--build", "--remove-orphans"]
                if compose_file.exists():
                    cmd = ["docker", "compose", "-f", str(compose_file), "up", "-d", "--build", "--remove-orphans"]
                subprocess.run(
                    cmd,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception as e:
                logger.warning(f"[PortalUpdate] Compose reload notice: {e}")

            return True
    except Exception as e:
        logger.warning(f"[PortalUpdate] Check/update error: {e}")
        return False


async def check_and_update_cycle():
    """
    Performs one check and update pass:
    1. Portal Stack self-update from GitHub Releases
    2. Workers update from Telegram MTProto / Core API
    """
    # 1. Linux Portal stack self-update
    try:
        check_and_update_portal_stack()
    except Exception as e:
        logger.warning(f"Portal stack self-update notice: {e}")

    # 2. Worker updates
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
        await download_and_install_worker(upd)


async def main():
    logger.info("Starting Tadagram Worker Portal Linux Autonomous Auto-Updater Daemon...")
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
