from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

RELAUNCHED_ENV = "GAPS_SDK_RELAUNCHED"


def _run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def _build_versions(binary: Path) -> tuple[str, str] | None:
    minos = sdk = None
    for line in _run("vtool", "-show-build", str(binary)).splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "minos":
            minos = parts[1]
        elif len(parts) == 2 and parts[0] == "sdk":
            sdk = parts[1]
    return (minos, sdk) if minos and sdk else None


def _patched_interpreter(source: Path, sdk: str) -> Path | None:
    target = Path(sys.prefix) / "bin" / f"python-sdk{sdk}"
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return target
    versions = _build_versions(source)
    if versions is None:
        return None
    if versions[1] == sdk:
        return source
    staging = target.with_name(f"{target.name}.tmp")
    shutil.copy2(source, staging)
    _run("vtool", "-set-build-version", "macos", versions[0], sdk, "-replace", "-output", str(staging), str(staging))
    _run("codesign", "--force", "-s", "-", str(staging))
    staging.replace(target)
    return target


def relaunch_with_latest_sdk() -> None:
    if sys.platform != "darwin" or getattr(sys, "frozen", False) or os.environ.get(RELAUNCHED_ENV):
        return
    if sys.prefix == sys.base_prefix:
        return
    source = Path(os.path.realpath(sys.executable))
    try:
        sdk = _run("xcrun", "--show-sdk-version").strip()
        interpreter = _patched_interpreter(source, sdk)
    except (OSError, subprocess.CalledProcessError):
        return
    if interpreter is None or interpreter == source:
        return
    os.environ[RELAUNCHED_ENV] = "1"
    os.execv(interpreter, [str(interpreter), *sys.orig_argv[1:]])
