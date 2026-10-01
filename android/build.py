"""Builds android/build/jarvis.apk straight with the Android SDK's own tools - no Gradle.

Needs a JDK (javac, keytool) and the Android SDK with build-tools and a platform:
    sdkmanager build-tools/36.0.0 platforms/android-36
The SDK is found through ANDROID_HOME, else %LOCALAPPDATA%\\Android\\Sdk.

    python android/build.py

Signed with the standard debug key (~/.android/debug.keystore, made on first build)
so a new build installs over the old one. Jarvis's panel serves the result at
http://computer:8765/jarvis.apk - open that on the phone to install.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
ICON = HERE.parent / "backend" / "static" / "panel" / "icon.png"
KEYSTORE = Path.home() / ".android" / "debug.keystore"
MIN_SDK = "24"


def sdk_root() -> Path:
    for candidate in (os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
                      Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk"):
        if candidate and Path(candidate, "build-tools").is_dir():
            return Path(candidate)
    sys.exit("Android SDK не найден: поставь ANDROID_HOME или build-tools в %LOCALAPPDATA%\\Android\\Sdk")


def newest(folder: Path, pattern: str) -> Path:
    def version(p: Path) -> tuple[int, ...]:
        return tuple(int(n) for n in re.findall(r"\d+", p.name))

    found = sorted((p for p in folder.glob(pattern) if p.is_dir()), key=version)
    if not found:
        sys.exit(f"В {folder} нет {pattern}")
    return found[-1]


def tool(folder: Path, name: str) -> str:
    for suffix in ("", ".exe", ".bat"):
        if (folder / (name + suffix)).exists():
            return str(folder / (name + suffix))
    sys.exit(f"Нет {name} в {folder}")


def jdk(name: str) -> str:
    home = os.environ.get("JAVA_HOME")
    if home and Path(home, "bin").is_dir():
        return tool(Path(home, "bin"), name)
    found = shutil.which(name)
    if not found:
        sys.exit(f"Нет {name}: нужен JDK 17")
    return found


def run(*args: str) -> None:
    print(">", Path(args[0]).name, *args[1:4], "..." if len(args) > 4 else "")
    subprocess.run(args, check=True)


def main() -> None:
    sdk = sdk_root()
    tools = newest(sdk / "build-tools", "*")
    platform = newest(sdk / "platforms", "android-[0-9]*")
    android_jar = str(platform / "android.jar")
    print(f"SDK {sdk}\nbuild-tools {tools.name}, {platform.name}")

    shutil.rmtree(BUILD, ignore_errors=True)
    res = BUILD / "res"
    shutil.copytree(HERE / "res", res)
    (res / "mipmap-xxxhdpi").mkdir(parents=True)
    shutil.copy(ICON, res / "mipmap-xxxhdpi" / "ic_launcher.png")  # the panel's own icon - one picture for both

    aapt2 = tool(tools, "aapt2")
    run(aapt2, "compile", "--dir", str(res), "-o", str(BUILD / "res.zip"))
    run(aapt2, "link", "-o", str(BUILD / "base.apk"), "-I", android_jar,
        "--manifest", str(HERE / "AndroidManifest.xml"), "-A", str(HERE / "assets"),
        "--min-sdk-version", MIN_SDK, str(BUILD / "res.zip"))

    classes = BUILD / "classes"
    classes.mkdir()
    sources = [str(p) for p in (HERE / "src").rglob("*.java")]
    boot = os.pathsep.join([android_jar, str(tools / "core-lambda-stubs.jar")])  # the stubs: for lambdas
    run(jdk("javac"), "-source", "8", "-target", "8", "-bootclasspath", boot,
        "-Xlint:-options", "-encoding", "UTF-8", "-d", str(classes), *sources)
    dex = BUILD / "dex"
    dex.mkdir()
    run(tool(tools, "d8"), "--release", "--min-api", MIN_SDK, "--lib", android_jar,
        "--output", str(dex), *[str(p) for p in classes.rglob("*.class")])

    shutil.copy(BUILD / "base.apk", BUILD / "unsigned.apk")
    with zipfile.ZipFile(BUILD / "unsigned.apk", "a", zipfile.ZIP_DEFLATED) as apk:
        apk.write(dex / "classes.dex", "classes.dex")
    run(tool(tools, "zipalign"), "-p", "-f", "4", str(BUILD / "unsigned.apk"), str(BUILD / "aligned.apk"))

    if not KEYSTORE.exists():  # the standard debug key: its password is the well-known "android"
        KEYSTORE.parent.mkdir(parents=True, exist_ok=True)
        run(jdk("keytool"), "-genkeypair", "-keystore", str(KEYSTORE), "-storepass", "android",
            "-alias", "androiddebugkey", "-keypass", "android", "-keyalg", "RSA", "-keysize", "2048",
            "-validity", "10000", "-dname", "CN=Android Debug,O=Android,C=US")
    apk = BUILD / "jarvis.apk"
    run(tool(tools, "apksigner"), "sign", "--ks", str(KEYSTORE), "--ks-pass", "pass:android",
        "--key-pass", "pass:android", "--out", str(apk), str(BUILD / "aligned.apk"))
    print(f"\nГотово: {apk} ({apk.stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    main()
