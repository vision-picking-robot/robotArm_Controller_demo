#!/bin/bash
# Build a Mac diagnostic runtime. Never connect to the robot in this step.
set -euo pipefail
[[ "$(uname -sm)" == 'Darwin arm64' ]] || { echo 'Apple Silicon macOS required.' >&2; exit 1; }
repo=$(cd "$(dirname "$0")/../.." && pwd)
cache="$repo/.cache/e0509-host-sdk"
cm=${CMAKE:-cmake}
gm=${GMAKE:-gmake}
command -v docker >/dev/null || { echo "Missing: docker" >&2; exit 1; }
mkdir -p "$cache"
src=$(mktemp -d)
trap 'rm -rf "$src"' EXIT
fetch() {
    git init -q "$src/$1"
    git -C "$src/$1" fetch -q --depth 1 "https://github.com/$2.git" "$3"
    git -C "$src/$1" checkout -q --detach "$3"
}
if [[ ! -x "$cache/blink" ]]; then
    for tool in "$cm" "$gm" git; do command -v "$tool" >/dev/null || { echo "Missing: $tool" >&2; exit 1; }; done
    fetch shim jiixyj/epoll-shim 18159584bb3d17e601b9315a7398ace018251bdc
    fetch blink jart/blink f006a4fc6f9b8de9272504fdff0dbbe5ce5dc580
    git -C "$src/blink" apply "$repo/rosbridge/host-sdk/blink-macos.patch"
    "$cm" -S "$src/shim" -B "$src/build" -DBUILD_TESTING=OFF -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$cache/prefix"
    "$cm" --build "$src/build" -j4
    "$cm" --install "$src/build"
    (
        cd "$src/blink"
        CPPFLAGS="-I$cache/prefix/include/libepoll-shim" LDFLAGS="-L$cache/prefix/lib -Wl,-rpath,$cache/prefix/lib" LDLIBS=-lepoll-shim ./configure
        grep -qx '#define HAVE_EPOLL_PWAIT1' config.h
        "$gm" -j4 o//blink/blink
    )
    cp "$src/blink/o/blink/blink" "$cache/blink"
fi
# Docker supplies the existing verified Linux SDK and libraries; the build has no robot network.
img=yolov8-e0509-driver:humble-gl010117
if ! docker image inspect "$img" >/dev/null 2>&1; then (cd "$repo" && docker compose build doosan-driver); fi
mkdir -p "$cache/linux"
docker run --rm --platform linux/amd64 --network none --entrypoint bash \
    -v "$repo/rosbridge/host-sdk/access-worker.cpp:/worker.cpp:ro" \
    -v "$repo/rosbridge/host-sdk/rh_p12.hpp:/rh_p12.hpp:ro" \
    -v "$repo/rosbridge/host-sdk/scenario_worker.hpp:/scenario_worker.hpp:ro" \
    -v "$repo/rosbridge/check-controller.cpp:/probe.cpp:ro" "$img" -euc '
    cd /doosan_ws
    sha256sum -c gl010117.sha256 >&2
    p=/usr/local/bin/check-e0509
    w=/usr/local/bin/e0509-access-worker
    build() { g++ -std=c++17 -Isrc/common2/include "$1" /opt/doosan/lib/libDRFL.a /opt/doosan/lib/libPocoNet.so.62 /opt/doosan/lib/libPocoFoundation.so.62 -pthread -o "$2"; }
    build /probe.cpp "$p"
    build /worker.cpp "$w"
    { echo "$p"; echo "$w"; echo /lib64/ld-linux-x86-64.so.2; ldd "$p" | awk "/=> \\// {print \$3}"; } | sort -u | tar -chf - -T -
' | tar -xf - -C "$cache/linux"
test "$(python3 "$repo/scripts/check_robot_sdk_host.py" --sdk-version)" = GL010117
echo 'Prepared GL010117; no robot connection was made.'
