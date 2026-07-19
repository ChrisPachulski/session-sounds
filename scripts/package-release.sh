#!/bin/sh
set -eu

fail() {
    printf 'session-sounds packager: %s\n' "$*" >&2
    exit 1
}

require_tool() {
    command -v "$1" >/dev/null 2>&1 || fail "required tool '$1' was not found on PATH"
}

[ "$#" -eq 3 ] || fail "usage: package-release.sh VERSION INPUTS_DIR OUTPUT_DIR"
VERSION=$1
INPUTS_DIR=$2
OUTPUT_DIR=$3

printf '%s\n' "$VERSION" | grep -Eq '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$' \
    || fail "VERSION must be a stable semantic version without a leading v"
[ -d "$INPUTS_DIR" ] || fail "input directory '$INPUTS_DIR' does not exist"
[ ! -e "$OUTPUT_DIR" ] || fail "output path '$OUTPUT_DIR' already exists"

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P) || fail "could not resolve script directory"
REPOSITORY_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd -P) || fail "could not resolve repository root"
LICENSE_FILE="$REPOSITORY_ROOT/LICENSE"
[ -f "$LICENSE_FILE" ] && [ ! -L "$LICENSE_FILE" ] || fail "repository LICENSE is missing or unsafe"

require_tool tar
require_tool zip
if command -v sha256sum >/dev/null 2>&1; then
    HASH_TOOL=sha256sum
elif command -v shasum >/dev/null 2>&1; then
    HASH_TOOL=shasum
else
    fail "required checksum tool was not found; install sha256sum or shasum"
fi

OUTPUT_PARENT=$(dirname -- "$OUTPUT_DIR")
mkdir -p "$OUTPUT_PARENT" || fail "could not create output parent '$OUTPUT_PARENT'"
STAGE_DIR=$(mktemp -d "$OUTPUT_PARENT/.session-sounds-release.XXXXXX") || fail "could not create release staging directory"
WORK_DIR=
cleanup() {
    [ -z "${STAGE_DIR:-}" ] || rm -rf "$STAGE_DIR"
    [ -z "${WORK_DIR:-}" ] || rm -rf "$WORK_DIR"
}
trap cleanup EXIT HUP INT TERM
WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/session-sounds-package.XXXXXX") || fail "could not create package work directory"

for TARGET in \
    aarch64-apple-darwin \
    x86_64-apple-darwin \
    aarch64-unknown-linux-gnu \
    x86_64-unknown-linux-gnu
do
    SOURCE="$INPUTS_DIR/binary-${TARGET}/session-sounds"
    [ -f "$SOURCE" ] && [ ! -L "$SOURCE" ] || fail "missing regular binary '$SOURCE'"
    PACKAGE_DIR="$WORK_DIR/$TARGET"
    mkdir -p "$PACKAGE_DIR"
    install -m 0755 "$SOURCE" "$PACKAGE_DIR/session-sounds"
    cp "$LICENSE_FILE" "$PACKAGE_DIR/LICENSE"
    tar -C "$PACKAGE_DIR" -czf "$STAGE_DIR/session-sounds-v${VERSION}-${TARGET}.tar.gz" session-sounds LICENSE \
        || fail "could not package target '$TARGET'"
done

TARGET=x86_64-pc-windows-msvc
SOURCE="$INPUTS_DIR/binary-${TARGET}/session-sounds.exe"
[ -f "$SOURCE" ] && [ ! -L "$SOURCE" ] || fail "missing regular binary '$SOURCE'"
PACKAGE_DIR="$WORK_DIR/$TARGET"
mkdir -p "$PACKAGE_DIR"
cp "$SOURCE" "$PACKAGE_DIR/session-sounds.exe"
cp "$LICENSE_FILE" "$PACKAGE_DIR/LICENSE"
(CDPATH='' cd -- "$PACKAGE_DIR" && zip -q "$STAGE_DIR/session-sounds-v${VERSION}-${TARGET}.zip" session-sounds.exe LICENSE) \
    || fail "could not package target '$TARGET'"

if [ "$HASH_TOOL" = sha256sum ]; then
    (CDPATH='' cd -- "$STAGE_DIR" && sha256sum session-sounds-v* > SHA256SUMS && sha256sum --check SHA256SUMS) \
        || fail "release checksum generation or validation failed"
else
    (CDPATH='' cd -- "$STAGE_DIR" && shasum -a 256 session-sounds-v* > SHA256SUMS && shasum -a 256 -c SHA256SUMS) \
        || fail "release checksum generation or validation failed"
fi

[ "$(find "$STAGE_DIR" -type f | wc -l | tr -d ' ')" -eq 6 ] \
    || fail "release staging did not contain exactly five archives and SHA256SUMS"
mv "$STAGE_DIR" "$OUTPUT_DIR" || fail "could not publish packaged output to '$OUTPUT_DIR'"
STAGE_DIR=
printf 'Packaged Session Sounds %s into %s.\n' "$VERSION" "$OUTPUT_DIR"
