#!/usr/bin/env bash
#
# Compile every translation (.ts) in this folder into the .qm files that
# VRD Next loads.  Run it after translating or updating a .ts.
#
set -e
cd "$(dirname "$0")"

shopt -s nullglob
found=0
for ts in vrd-next_*.ts; do
    [ "$ts" = "vrd-next_en.ts" ] && continue   # English is the built-in default
    found=1
    echo "Compiling $ts"
    pyside6-lrelease "$ts"
done

if [ "$found" -eq 0 ]; then
    echo "No vrd-next_*.ts files found here."
fi
