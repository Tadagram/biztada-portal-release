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
    echo "Current version: $CURRENT_VERSION"
    read -p "Enter new version (e.g. 1.0.1): " NEW_VERSION
fi

if [ -z "$NEW_VERSION" ]; then
    echo "Version cannot be empty!"
    exit 1
fi

echo "Bumping version: $CURRENT_VERSION -> $NEW_VERSION"
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
echo ""
echo "To publish on GitHub:"
echo "  1. git tag -a v${NEW_VERSION} -m 'Release v${NEW_VERSION}'"
echo "  2. git push origin v${NEW_VERSION}"
echo "  3. gh release create v${NEW_VERSION} $ARCHIVE_NAME --title 'Worker Portal Linux v${NEW_VERSION}' --notes-file CHANGELOG.md"
