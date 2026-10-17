"""Безопасное удаление: Корзина Windows, а на съёмных/сетевых дисках (где корзины нет) — перенос в _rejected."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from send2trash import send2trash

REJECTED_DIR = "_rejected"

DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_REMOTE = 2, 3, 4


def drive_type(path: Path) -> int:
    if sys.platform != "win32":
        return DRIVE_FIXED
    import ctypes

    anchor = Path(path).resolve().anchor
    return ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(anchor))


def has_recycle_bin(path: Path) -> bool:
    return drive_type(path) == DRIVE_FIXED


def describe_target(root: Path) -> str:
    if has_recycle_bin(root):
        return "recycle"
    return "folder"


def remove(abs_path: Path, root: Path) -> None:
    if not abs_path.exists():
        return
    if has_recycle_bin(abs_path):
        send2trash(str(abs_path))
        return
    rel = abs_path.relative_to(root)
    dest = root / REJECTED_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    n = 1
    while dest.exists():
        dest = dest.with_name(f"{abs_path.stem} ({n}){abs_path.suffix}")
        n += 1
    shutil.move(str(abs_path), str(dest))


def reveal(abs_path: Path) -> None:
    if sys.platform == "win32":
        import subprocess

        subprocess.Popen(["explorer", "/select,", str(abs_path)])


def open_external(abs_path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(abs_path))  # noqa: S606
