#!/usr/bin/env bash
# ==============================================================================
#  Tadagram Worker Portal Linux - GitHub Release & Packaging Script
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

VERSION_FILE="$DIR/VERSION"
CURRENT_VERSION=$(cat "$VERSION_FILE" | tr -d ' \r\n')
NEW_VERSION="${1:-}"

if [ -z "$NEW_VERSION" ]; then
    NEW_VERSION="$CURRENT_VERSION"
fi

echo "Packaging Worker Portal Linux v$NEW_VERSION..."
echo "$NEW_VERSION" > "$VERSION_FILE"

# Package archive
ARCHIVE_NAME="tadagram-worker-portal-linux-v${NEW_VERSION}.tar.gz"
echo "Creating release bundle: $ARCHIVE_NAME..."
tar -czf "$DIR/$ARCHIVE_NAME" \
    --exclude=".git" \
    --exclude="data/workers/*" \
    --exclude="data/shared/*" \
    --exclude="*.tar.gz" \
    -C "$DIR" .

echo "Release archive created successfully at $DIR/$ARCHIVE_NAME"

DIST_DIR="$DIR/../dist"
if [ -d "$DIST_DIR" ]; then
    cp -f "$DIR/$ARCHIVE_NAME" "$DIST_DIR/$ARCHIVE_NAME"
    cp -f "$DIR/$ARCHIVE_NAME" "$DIST_DIR/tadagram-worker-portal-linux.tar.gz"
    echo "Successfully synced to $DIST_DIR"
fi

echo ""
echo "To publish on GitHub:"
echo "  1. git tag -a v${NEW_VERSION} -m 'Release v${NEW_VERSION}'"
echo "  2. git push origin v${NEW_VERSION}"
echo "  3. gh release create v${NEW_VERSION} $ARCHIVE_NAME --title 'Worker Portal Linux v${NEW_VERSION}' --notes-file CHANGELOG.md"

