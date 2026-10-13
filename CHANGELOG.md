# Changelog - Worker Portal Linux

All notable changes to the Tadagram Worker Portal for Linux will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.1] - 2026-10-13

### Added
- **Server-Allocated Device & Worker Identity**:
  - Full support for authoritative UUID allocation via Core API bootstrap (`/internal/worker-portal/devices/bootstrap`).
  - Automatic environment injection of `PORTAL_DEVICE_ID`, `PORTAL_WORKER_UUID`, and `CORE_API_URL` across worker runners and supervisor containers.
- **Cross-Platform Portal Distribution**:
  - Official inclusion of the Windows Production Setup installer (`TadaBusiness_Setup.exe`) in release assets.
  - Automated sync of release archives (`.tar.gz`, `.zip`, and `.exe`) to the centralized repository `workers/dist/`.
- **Packaging & Release Automation**:
  - Improved `scripts/release.ps1` and `scripts/release.sh` with non-interactive versioning and checksum verification.

## [1.0.0] - 2026-10-06


### Added
- **Unified Linux Headless Portal**: Production-grade Docker Compose architecture for hosting and supervising all Tadagram worker micro-services on Linux servers and VPS.
- **Automated Linux Worker Updater Daemon (`worker_updater.py`)**:
  - Automatically queries Telegram release channel `@tadabiz_release_manager` and svc-core-api `/api/updates/worker-releases`.
  - Native platform detection for Linux worker release naming formats:
    - `{worker_id}-linux_v{version}.wrkx`
    - `{worker_id}_linux_v{version}.wrkx`
    - `{worker_id}_v{version}-linux.wrkx`
    - `{worker_id}_v{version}.wrkx` (Universal)
  - Direct MTProto and HTTP fast-path file download engine bypassing backend bottlenecks.
  - AES-256-GCM encrypted payload decryption and atomic in-place binary upgrades.
  - Zero-downtime worker process restart and supervisor signaling.
- **Remote Desktop & Visual Supervision**:
  - Integrated Xvfb virtual display with TigerVNC and WebSocket noVNC web streaming (ports 6080, 6081, 6082).
  - Cloudflare Quick Tunnels for external zero-trust operator access.
- **One-Line Installer (`scripts/install.sh`)**:
  - Automated environment check, Docker setup, dependencies installation, and systemd service creation for Ubuntu/Debian.
- **Release Automation (`scripts/release.sh`, `scripts/release.ps1`)**:
  - Automated semver bumping, changelog updates, multi-arch Docker image compilation, and GitHub release tagging.
