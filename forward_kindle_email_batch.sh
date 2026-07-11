#!/bin/zsh
set -euo pipefail

if (( $# != 3 )); then
  print -u2 "usage: $0 PROFILE_NAME OFFSET LIMIT"
  exit 64
fi

PROFILE_NAME=$1
OFFSET=$2
LIMIT=$3
if [[ -z $PROFILE_NAME || $PROFILE_NAME == *'/'* ]]; then
  print -u2 "PROFILE_NAME must be the exact single directory name reported by discovery"
  exit 64
fi
if [[ ! $OFFSET =~ '^[0-9]+$' ]] || [[ ! $LIMIT =~ '^[1-9][0-9]*$' ]]; then
  print -u2 "OFFSET must be >= 0 and LIMIT must be >= 1"
  exit 64
fi
if (( LIMIT > 299 )); then
  print -u2 "LIMIT cannot exceed Fire Gallery's observed 299-item ceiling"
  exit 64
fi

ADB=${ADB:-$(command -v adb)}
exec "$ADB" shell am start --user 0 -S -W \
  -n io.github.kindlekidsphotoexport/.ExportActivity \
  --ez forward_to_email true \
  --es profile_name "$PROFILE_NAME" \
  --ei offset "$OFFSET" \
  --ei limit "$LIMIT"
