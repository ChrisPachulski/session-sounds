#!/bin/sh
set -eu

REPOSITORY="ChrisPachulski/session-sounds"

fail() {
    printf 'session-sounds installer: %s\n' "$*" >&2
    exit 1
}

require_tool() {
    command -v "$1" >/dev/null 2>&1 || fail "required tool '$1' was not found on PATH"
}

read_version() {
    awk '
        /^\[package\][[:space:]]*$/ { package = 1; next }
        /^\[/ && package { exit }
        package && /^[[:space:]]*version[[:space:]]*=/ {
            value = $0
            sub(/^[^=]*=[[:space:]]*"/, "", value)
            sub(/"[[:space:]]*$/, "", value)
            print value
            exit
        }
    ' "$1"
}

resolve_target() {
    os=$(uname -s) || fail "could not detect the operating system with uname"
    arch=$(uname -m) || fail "could not detect the CPU architecture with uname"
    case "$os:$arch" in
        Darwin:arm64 | Darwin:aarch64) printf '%s\n' 'aarch64-apple-darwin' ;;
        Darwin:x86_64) printf '%s\n' 'x86_64-apple-darwin' ;;
        Linux:arm64 | Linux:aarch64) printf '%s\n' 'aarch64-unknown-linux-gnu' ;;
        Linux:x86_64 | Linux:amd64) printf '%s\n' 'x86_64-unknown-linux-gnu' ;;
        *) fail "unsupported platform '$os/$arch'; supported: macOS arm64/x86_64 and Linux arm64/x86_64" ;;
    esac
}

download_file() {
    url=$1
    destination=$2
    if ! curl --fail --location --retry 3 --retry-delay 1 --retry-connrefused \
        --output "$destination" "$url"; then
        fail "download failed: $url"
    fi
}

verify_checksum() {
    archive=$1
    checksums=$2
    asset=$3
    expected=$(awk -v asset="$asset" '
        {
            hash = $1
            file = $2
            sub(/^\*/, "", file)
            if (file == asset) {
                count++
                selected = hash
            }
        }
        END {
            if (count != 1 || selected !~ /^[0-9A-Fa-f]{64}$/) exit 1
            print tolower(selected)
        }
    ' "$checksums") || fail "SHA256SUMS has no single valid checksum for '$asset'"
    if command -v sha256sum >/dev/null 2>&1; then
        actual=$(sha256sum "$archive" | awk '{ print tolower($1) }')
    elif command -v shasum >/dev/null 2>&1; then
        actual=$(shasum -a 256 "$archive" | awk '{ print tolower($1) }')
    else
        fail "required checksum tool was not found; install sha256sum or shasum"
    fi
    [ "$actual" = "$expected" ] || fail "checksum mismatch for '$asset'; the existing binary was not changed"
}

extract_archive() {
    archive=$1
    destination=$2
    members_file="${destination}.members"
    tar -tzf "$archive" > "$members_file" || fail "could not inspect '$archive'"
    awk '
        $0 == "session-sounds" { binary++; next }
        $0 == "LICENSE" { license++; next }
        { unsafe = 1 }
        END { exit !(binary == 1 && license == 1 && !unsafe) }
    ' "$members_file" || fail "unsafe archive members; expected exactly session-sounds and LICENSE"
    mkdir -p "$destination"
    tar -xzf "$archive" -C "$destination" || fail "could not extract '$archive'"
    [ -f "$destination/session-sounds" ] && [ ! -L "$destination/session-sounds" ] \
        || fail "archive binary is not a regular non-symlink session-sounds file"
    [ -f "$destination/LICENSE" ] && [ ! -L "$destination/LICENSE" ] \
        || fail "archive LICENSE is not a regular non-symlink file"
    chmod 0755 "$destination/session-sounds" || fail "could not make the staged binary executable"
}

install_binary() {
    prepared=$1
    destination=$2
    if [ -e "$destination" ] && [ ! -f "$destination" ]; then
        fail "install destination '$destination' exists and is not a regular file"
    fi
    mv -f "$prepared" "$destination" || fail "could not atomically replace '$destination'"
}

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P) || fail "could not resolve the installer directory"
PLUGIN_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd -P) || fail "could not resolve the plugin root"
VERSION=$(read_version "$PLUGIN_ROOT/Cargo.toml")
case "$VERSION" in
    '' | v*) fail "Cargo.toml contains an invalid release version '$VERSION'" ;;
esac
TARGET=$(resolve_target)
ASSET="session-sounds-v${VERSION}-${TARGET}.tar.gz"
BASE_URL="https://github.com/${REPOSITORY}/releases/download/v${VERSION}"

require_tool curl
require_tool tar
mkdir -p "$PLUGIN_ROOT/bin" || fail "could not create '$PLUGIN_ROOT/bin'"
STAGE_DIR=$(mktemp -d "$PLUGIN_ROOT/bin/.session-sounds-install.XXXXXX") || fail "could not create a staging directory"
cleanup() {
    rm -rf "$STAGE_DIR"
}
trap cleanup EXIT HUP INT TERM

ARCHIVE="$STAGE_DIR/$ASSET"
CHECKSUMS="$STAGE_DIR/SHA256SUMS"
EXTRACTED="$STAGE_DIR/extracted"
download_file "$BASE_URL/$ASSET" "$ARCHIVE"
download_file "$BASE_URL/SHA256SUMS" "$CHECKSUMS"
verify_checksum "$ARCHIVE" "$CHECKSUMS" "$ASSET"
extract_archive "$ARCHIVE" "$EXTRACTED"
install_binary "$EXTRACTED/session-sounds" "$PLUGIN_ROOT/bin/session-sounds"
printf 'Installed Session Sounds %s for %s.\n' "$VERSION" "$TARGET"
