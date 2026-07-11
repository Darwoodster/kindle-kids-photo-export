# Building the helper APK from source

The helper is a single Java Activity with no third-party runtime dependencies.
It deliberately requests no internet permission.

## Install build prerequisites on macOS

```sh
brew install openjdk android-commandlinetools
export JAVA_HOME="$(brew --prefix openjdk)"
export ANDROID_SDK_ROOT="$HOME/Library/Android/sdk"
yes | sdkmanager --licenses
sdkmanager 'platforms;android-35' 'build-tools;35.0.0'
```

Build and sign a locally installable APK:

```sh
./build_apk.sh
cp build/kindle-kids-photo-export.apk ./kindle-kids-photo-export.apk
```

The script generates a one-off signing key inside ignored `build/`, verifies the
signature, and prints the APK's SHA-256. Keep that key only if you need Android
to install later builds as in-place upgrades. Otherwise, uninstall the earlier
helper before installing a build signed by a different key.

The build uses only `javac`, `jar`, Android `d8`, `aapt`, `zipalign`,
`apksigner`, `keytool`, and `openssl`; Gradle is not required.
