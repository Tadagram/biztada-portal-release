#!/bin/bash
set -e

WORKER_ID="${WORKER_ID:-unknown}"
echo "========================================================="
echo " Starting Tadagram Worker Runner: $WORKER_ID"
echo "========================================================="

# Check for custom run script or executable binary
if [ -f "/app/worker/run.sh" ]; then
    echo "Executing /app/worker/run.sh..."
    exec bash /app/worker/run.sh
elif [ -x "/app/worker/${WORKER_ID}" ]; then
    echo "Found executable binary /app/worker/${WORKER_ID}, running directly..."
    exec /app/worker/${WORKER_ID}
elif [ -f "/app/worker/${WORKER_ID}.wrkx" ]; then
    echo "Found worker binary /app/worker/${WORKER_ID}.wrkx"
    # Decrypt and run if runtime loader is present
    exec python -m tadagram_runner /app/worker/${WORKER_ID}.wrkx
else
    echo "No custom binary mounted yet. Keeping container alive for Updater deployment..."
    exec tail -f /dev/null
fi
