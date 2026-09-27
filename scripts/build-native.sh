#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUILD_DIR=${BUILD_DIR:-"$PROJECT_DIR/build/native"}
INSTALL_PREFIX=${INSTALL_PREFIX:-"$PROJECT_DIR"}
ENCODEC_CPP_DIR=${ENCODEC_CPP_DIR:-"$BUILD_DIR/upstream/encodec.cpp"}
ENCODEC_CPP_UPDATE=${ENCODEC_CPP_UPDATE:-1}

if [ "$ENCODEC_CPP_UPDATE" = 1 ]; then
    ENCODEC_CPP_REVISION=$(
        "$PROJECT_DIR/scripts/update-encodec-cpp.sh" "$ENCODEC_CPP_DIR"
    )
elif [ ! -f "$ENCODEC_CPP_DIR/src/lib/encodec.cpp" ]; then
    echo "ENCODEC_CPP_UPDATE=0 requires an existing checkout at $ENCODEC_CPP_DIR" >&2
    exit 1
else
    ENCODEC_CPP_REVISION=$(git -C "$ENCODEC_CPP_DIR" rev-parse HEAD 2>/dev/null || echo unknown)
fi

mkdir -p "$BUILD_DIR"
printf '%s\n' "$ENCODEC_CPP_REVISION" > "$BUILD_DIR/encodec-cpp-revision.txt"
echo "Building with encodec.cpp revision $ENCODEC_CPP_REVISION"

cmake -S "$PROJECT_DIR/native" -B "$BUILD_DIR" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$INSTALL_PREFIX" \
    -DENCODEC_CPP_SOURCE_DIR="$ENCODEC_CPP_DIR"
cmake --build "$BUILD_DIR" --parallel
cmake --install "$BUILD_DIR"

echo "Installed native worker: $INSTALL_PREFIX/bin/encodec-live-native"
