#!/bin/zsh
set -euo pipefail

ROOT=${0:A:h}
ANDROID_SDK_ROOT=${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}
if [[ -z $ANDROID_SDK_ROOT ]]; then
  print -u2 "Set ANDROID_SDK_ROOT to an Android SDK containing a platform and build-tools."
  exit 64
fi

ANDROID_JAR=$(find "$ANDROID_SDK_ROOT/platforms" -name android.jar -type f | sort -V | tail -1)
BUILD_TOOLS=$(find "$ANDROID_SDK_ROOT/build-tools" -mindepth 1 -maxdepth 1 -type d | sort -V | tail -1)
if [[ -z $ANDROID_JAR || -z $BUILD_TOOLS ]]; then
  print -u2 "Android platform or build-tools not found under $ANDROID_SDK_ROOT"
  exit 69
fi

JAVAC=${JAVAC:-${JAVA_HOME:+$JAVA_HOME/bin/javac}}
KEYTOOL=${KEYTOOL:-${JAVA_HOME:+$JAVA_HOME/bin/keytool}}
JAR=${JAR:-${JAVA_HOME:+$JAVA_HOME/bin/jar}}
JAVAC=${JAVAC:-$(command -v javac)}
KEYTOOL=${KEYTOOL:-$(command -v keytool)}
JAR=${JAR:-$(command -v jar)}

BUILD="$ROOT/build"
rm -rf "$BUILD"
mkdir -p "$BUILD/classes" "$BUILD/dex"

"$JAVAC" -source 8 -target 8 -bootclasspath "$ANDROID_JAR" \
  -d "$BUILD/classes" \
  "$ROOT/app/src/main/java/io/github/kindlekidsphotoexport/ExportActivity.java"
"$JAR" cf "$BUILD/classes.jar" -C "$BUILD/classes" .
"$BUILD_TOOLS/d8" --lib "$ANDROID_JAR" --min-api 22 \
  --output "$BUILD/dex" "$BUILD/classes.jar"
"$BUILD_TOOLS/aapt" package -f \
  -M "$ROOT/app/src/main/AndroidManifest.xml" \
  -I "$ANDROID_JAR" -F "$BUILD/unsigned.apk"
(cd "$BUILD/dex" && "$BUILD_TOOLS/aapt" add "$BUILD/unsigned.apk" classes.dex)
"$BUILD_TOOLS/zipalign" -f 4 "$BUILD/unsigned.apk" "$BUILD/aligned.apk"

KEYSTORE="$BUILD/local-build.keystore"
STOREPASS=$(openssl rand -hex 16)
"$KEYTOOL" -genkeypair -noprompt -keystore "$KEYSTORE" \
  -storepass "$STOREPASS" -keypass "$STOREPASS" -alias localbuild \
  -keyalg RSA -keysize 2048 -validity 10000 \
  -dname "CN=Local Build, OU=Kindle Kids Photo Export, O=Local, C=XX"
"$BUILD_TOOLS/apksigner" sign --ks "$KEYSTORE" \
  --ks-key-alias localbuild --ks-pass "pass:$STOREPASS" \
  --key-pass "pass:$STOREPASS" \
  --out "$BUILD/kindle-kids-photo-export.apk" "$BUILD/aligned.apk"
"$BUILD_TOOLS/apksigner" verify --verbose "$BUILD/kindle-kids-photo-export.apk"
shasum -a 256 "$BUILD/kindle-kids-photo-export.apk"
