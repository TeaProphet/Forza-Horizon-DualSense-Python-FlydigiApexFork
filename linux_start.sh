#!/usr/bin/env bash
# FH5 DualSense — Linux/macOS stub launcher.
# Downloads the latest GitHub release into ./app and runs it.
# Asks before updating to a newer version.
set -e

pause_exit() {
    local code=$?
    echo
    echo "App exited with code $code."
    read -r -p "Press Enter to close this window..." _ || true
    exit "$code"
}
trap pause_exit EXIT

REPO="HamzaYslmn/Forza-Horizon-DualSense-Python"
ROOT="$(cd "$(dirname "$0")" && pwd)"
APP="$ROOT/app"
VERSION_FILE="$APP/.version"

need() { command -v "$1" >/dev/null 2>&1; }

# --- Resolve latest release tag ---
LATEST=""
if need curl; then
    LATEST=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
        -H "User-Agent: fh5ds-launcher" 2>/dev/null \
        | grep -E '"tag_name"' | head -n1 | sed -E 's/.*"tag_name":\s*"([^"]+)".*/\1/')
elif need wget; then
    LATEST=$(wget -qO- --header="User-Agent: fh5ds-launcher" \
        "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
        | grep -E '"tag_name"' | head -n1 | sed -E 's/.*"tag_name":\s*"([^"]+)".*/\1/')
fi

SOURCE="release"
if [ -z "$LATEST" ]; then
    echo "No release found. Falling back to latest 'main' branch."
    LATEST="main"
    SOURCE="branch"
fi

CURRENT=""
[ -f "$VERSION_FILE" ] && CURRENT=$(cat "$VERSION_FILE")

install_release() {
    local tag="$1"
    local kind="$2"
    local zip="$ROOT/fh5ds-$tag.zip"
    local tmp="$ROOT/_extract"
    local url
    if [ "$kind" = "branch" ]; then
        url="https://github.com/$REPO/archive/refs/heads/$tag.zip"
    else
        url="https://github.com/$REPO/archive/refs/tags/$tag.zip"
    fi
    echo "Downloading $tag..."
    if need curl; then
        curl -fsSL "$url" -o "$zip"
    else
        wget -q "$url" -O "$zip"
    fi
    rm -rf "$tmp"
    mkdir -p "$tmp"
    if need unzip; then
        unzip -q "$zip" -d "$tmp"
    else
        python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$zip" "$tmp"
    fi
    rm -rf "$APP"
    mv "$tmp"/*/ "$APP"
    rm -rf "$tmp" "$zip"
    echo "$tag" > "$VERSION_FILE"
    echo "Installed $tag."
}

if [ -z "$CURRENT" ]; then
    echo "Installing $LATEST..."
    install_release "$LATEST" "$SOURCE"
elif [ "$SOURCE" = "branch" ]; then
    echo "Refreshing 'main' branch (installed: $CURRENT)..."
    install_release "$LATEST" "$SOURCE"
elif [ "$CURRENT" != "$LATEST" ]; then
    echo "Update available: $CURRENT -> $LATEST"
    read -r -p "Update now? [Y/n] " ans
    case "${ans:-Y}" in
        [Nn]*) ;;
        *) install_release "$LATEST" "$SOURCE" ;;
    esac
else
    echo "Up to date ($CURRENT)."
fi

# --- Ensure uv is available ---
if ! need uv; then
    echo "uv was not found."
    read -r -p "Install uv from https://astral.sh/uv/? [Y/n] " uvans
    case "${uvans:-Y}" in
        [Nn]*) python3 -m pip install --user uv ;;
        *)     curl -LsSf https://astral.sh/uv/install.sh | sh ;;
    esac
    if ! need uv; then
        echo "uv installed but not on PATH. Restart your shell or add ~/.local/bin to PATH."
        exit 1
    fi
fi

cd "$APP/src"
uv run main.py "$@"
