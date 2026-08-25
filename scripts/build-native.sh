#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUILD_DIR=${BUILD_DIR:-"$PROJECT_DIR/build/native"}
INSTALL_PREFIX=${INSTALL_PREFIX:-"$PROJECT_DIR"}

cmake -S "$PROJECT_DIR/native" -B "$BUILD_DIR" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$INSTALL_PREFIX"
cmake --build "$BUILD_DIR" --parallel
cmake --install "$BUILD_DIR"

echo "Installed native worker: $INSTALL_PREFIX/bin/encodec-live-native"
