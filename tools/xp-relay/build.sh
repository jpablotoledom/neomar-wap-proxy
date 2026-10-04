#!/bin/bash
# Cross-compiles neomar_relay.exe for Windows XP and later from Linux.
# Needs the mingw-w64 cross compiler (Debian/Ubuntu: apt install gcc-mingw-w64-i686).
# The subsystem/OS version flags keep the PE header at 5.01 so XP accepts it.
set -e
cd "$(dirname "$0")"
i686-w64-mingw32-gcc -O2 -s -static -Wall -Wextra -D_WIN32_WINNT=0x0501 \
    -Wl,--major-subsystem-version,5,--minor-subsystem-version,1,--major-os-version,5,--minor-os-version,1 \
    -o neomar_relay.exe neomar_relay.c -lws2_32 -liphlpapi
echo "Built $(pwd)/neomar_relay.exe"
