#!/usr/bin/env bash
# ==============================================================================
#  Tadagram Worker Portal Linux - Automated Server Installation Script
#  Supported OS: Ubuntu 20.04/22.04/24.04, Debian 11/12
# ==============================================================================
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}================================================================${NC}"
echo -e "${BLUE}        TADAGRAM WORKER PORTAL LINUX - INSTALLER                ${NC}"
echo -e "${BLUE}================================================================${NC}"

if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Error: This script must be run as root or with sudo.${NC}"
  exit 1
fi

INSTALL_DIR="/opt/tadagram-worker-portal"
echo -e "${GREEN}Target Installation Directory: ${INSTALL_DIR}${NC}"

# 1. Install prerequisites
echo -e "${BLUE}[1/5] Installing prerequisite packages...${NC}"
apt-get update -qq
apt-get install -y -qq curl wget git ca-certificates gnupg lsb-release jq ufw

# 2. Install Docker & Docker Compose if missing
if ! command -v docker &> /dev/null; then
    echo -e "${BLUE}[2/5] Installing Docker Engine & Docker Compose Plugin...${NC}"
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/$(lsb_release -is | tr '[:upper:]' '[:lower:]')/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$(lsb_release -is | tr '[:upper:]' '[:lower:]') $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
    echo -e "${GREEN}[2/5] Docker is already installed.${NC}"
fi

# 3. Setup portal directory
echo -e "${BLUE}[3/5] Setting up Portal workspace...${NC}"
mkdir -p "${INSTALL_DIR}/data/workers" "${INSTALL_DIR}/data/shared"
if [ -f "./docker-compose.yml" ]; then
    cp -rn . "${INSTALL_DIR}/" 2>/dev/null || true
elif [ ! -f "${INSTALL_DIR}/docker-compose.yml" ]; then
    echo -e "${BLUE}Cloning biztada-portal-release from repository...${NC}"
    git clone https://github.com/Tadagram/biztada-portal-release.git "${INSTALL_DIR}" 2>/dev/null || {

        echo -e "${YELLOW}Could not clone via HTTPS, falling back to local files or releases...${NC}"
    }
fi

# 4. Generate .env if not exists
if [ ! -f "${INSTALL_DIR}/.env" ]; then
    if [ -f "${INSTALL_DIR}/.env.example" ]; then
        cp "${INSTALL_DIR}/.env.example" "${INSTALL_DIR}/.env"
        echo -e "${YELLOW}Created ${INSTALL_DIR}/.env from template. Please configure credentials.${NC}"
    fi
fi

# 5. Create systemd service for Auto-Start on boot
echo -e "${BLUE}[4/5] Registering systemd service tadagram-worker-portal...${NC}"
cat << 'EOF' > /etc/systemd/system/tadagram-worker-portal.service
[Unit]
Description=Tadagram Worker Portal Linux Stack
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/tadagram-worker-portal
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable tadagram-worker-portal.service

echo -e "${BLUE}[5/5] Building & Launching containers...${NC}"
cd "${INSTALL_DIR}"
docker compose up -d --build

echo -e "${GREEN}================================================================${NC}"
echo -e "${GREEN} TADAGRAM WORKER PORTAL INSTALLED & STARTED SUCCESSFULLY!       ${NC}"
echo -e "${GREEN}================================================================${NC}"
echo -e " - Directory:    ${INSTALL_DIR}"
echo -e " - noVNC Web UI: http://$(curl -s ifconfig.me || echo 'YOUR_SERVER_IP'):6080/vnc.html"
echo -e " - Auto-Updater: Active in background"
echo -e " - Management:   /opt/tadagram-worker-portal/scripts/service.sh [start|stop|status|logs]"
