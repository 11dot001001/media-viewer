"""HTTP API + раздача интерфейса."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import matching, media, trash
from .journal import Journal
from .library import Library
from .phone import FolderDevice, PhoneLibrary, WpdDevice
from .phonefind import PhoneFinder

BASE = Path(__file__).resolve().parent.parent
STATIC = BASE / "static"
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)
RECENT_FILE = DATA / "recent.json"

app = FastAPI(title="Media Viewer")
app.add_middleware(GZipMiddleware, minimum_size=2048)


@app.middleware("http")
async def no_stale_ui(request, call_next):
    """Интерфейс (app.js, style.css) браузер должен перепроверять: иначе после обновления остаётся старая версия."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_lib: Library | None = None


def lib() -> Library:
    if _lib is None:
        raise HTTPException(409, "Папка не открыта")
    return _lib


def _recent() -> list[str]:
    try:
        return json.loads(RECENT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _push_recent(path: str) -> None:
    items = [path] + [p for p in _recent() if p.lower() != path.lower()]
    RECENT_FILE.write_text(json.dumps(items[:10], ensure_ascii=False), encoding="utf-8")


def open_library(path: str) -> Library:
    global _lib
    p = Path(path).expanduser()
    if not p.is_dir():
        raise HTTPException(400, f"Папка не найдена: {path}")
    if _lib is not None:
        if _lib.kind == "folder" and _lib.root == p.resolve():
            _lib.start_scan()
            return _lib
        _lib.stop()
    _lib = Library(p, DATA)
    _lib.start_scan()
    _push_recent(str(_lib.root))
    return _lib


# ---------------------------------------------------------------- модели

class OpenReq(BaseModel):
    path: str


class PhoneReq(BaseModel):
    name: str | None = None
    path: str | None = None  # parsing name устройства из /api/phones
    folder: str | None = None  # папка вместо телефона (для проверки и карт памяти)
    roots: list[str] | None = None  # с какими папками сверять (None — со всеми)


class RefsReq(BaseModel):
    roots: list[str] | None = None  # None — все папки


class StatusReq(BaseModel):
    ids: list[int]
    status: int


class SettingsReq(BaseModel):
    series_gap: float | None = None
    series_dist: int | None = None
    series_keep: str | None = None


# ---------------------------------------------------------------- маршруты

@app.get("/")
def index():
    # к ссылкам на app.js и style.css добавляем версию файла — браузер не возьмёт старую копию из кэша
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "style.css"):
        v = int((STATIC / name).stat().st_mtime)
        html = html.replace(f"/static/{name}\"", f"/static/{name}?v={v}\"")
    return Response(html, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})


@app.get("/api/state")
def state():
    s = _lib.settings() if _lib else None
    return {
        "root": _lib.title if _lib else None,
        "source": {"kind": _lib.kind, "title": _lib.title} if _lib else None,
        "journal": _lib.journal.count() if _lib else 0,
        "recent": _recent(),
        "scan": dict(_lib.status, scanning=_lib.scanning) if _lib else None,
        "settings": s.__dict__ if s else None,
        "trash_target": _lib.trash_target() if _lib else None,
        "tools": {"ffmpeg": bool(media.FFMPEG), "heif": media.HEIF_OK, "raw": media.rawpy is not None},
    }


@app.get("/api/scan")
def scan_status():
    return dict(lib().status, scanning=lib().scanning)


@app.post("/api/open")
def api_open(req: OpenReq):
    open_library(req.path)
    return state()


@app.get("/api/phones")
def phones():
    try:
        return {"devices": WpdDevice.list_devices()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Не удалось получить список устройств: {e}") from e


@app.post("/api/open-phone")
def api_open_phone(req: PhoneReq):
    global _lib
    if req.folder:
        if not Path(req.folder).is_dir():
            raise HTTPException(400, f"Папка не найдена: {req.folder}")
        device = FolderDevice(Path(req.folder), req.name)
    elif req.path:
        device = WpdDevice(req.path, req.name)
    else:
        raise HTTPException(400, "Не указано устройство")
    if _lib is not None:
        _lib.stop()
    _lib = PhoneLibrary(device, DATA)
    _lib.set_ref_roots(req.roots)
    _lib.start_scan()
    return state()


class FindReq(BaseModel):
    everywhere: bool = False
    wide: bool = False  # поиск по дате с окном ±26 ч для не найденных


@app.post("/api/phone-find")
def phone_find_start(req: FindReq | None = None):
    lb = lib()
    if lb.kind != "folder":
        raise HTTPException(409, "Поиск на телефоне — из корзины папки облака")
    if lb.finder is None or not lb.finder.running:
        lb.finder = PhoneFinder(lb, DATA, everywhere=bool(req and req.everywhere), wide=bool(req and req.wide))
        lb.finder.start()
    return lb.finder.status


@app.get("/api/stats")
def stats():
    """Сколько места освобождено: по всем папкам и история текущей."""
    j = _lib.journal if _lib is not None else Journal(DATA / "journal.db")
    res = j.stats(_lib.title if _lib is not None else None)
    res["source"] = _lib.title if _lib is not None else None
    res["folder_size"] = _lib.status.get("folder_size") if _lib is not None else None
    return res


@app.get("/api/phone-thumb/{mid}")
def phone_thumb(mid: int):
    lb = lib()
    data = lb.finder.thumbs.get(mid) if lb.finder else None
    if not data:
        raise HTTPException(404)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/phone-find")
def phone_find_status():
    lb = lib()
    return lb.finder.status if lb.finder else {"phase": "idle"}


@app.post("/api/phone/allow-copy")
def allow_copy():
    lb = lib()
    if lb.kind != "phone":
        raise HTTPException(409, "Открыт не телефон")
    lb.allow_copy = True
    lb.start_scan()
    return scan_status()


@app.get("/api/refs")
def refs():
    selected = _lib.ref_roots() if _lib is not None and _lib.kind == "phone" else None
    roots = [dict(r, selected=selected is None or r["root"] in selected)
             for r in matching.library_roots(DATA)]
    return {"roots": roots, "journal": Journal(DATA / "journal.db").count()}


@app.post("/api/refs")
def set_refs(req: RefsReq):
    lb = lib()
    if lb.kind != "phone":
        raise HTTPException(409, "Сверка доступна только для телефона")
    if lb.scanning:
        raise HTTPException(409, "Дождитесь окончания сканирования")
    lb.set_ref_roots(req.roots)
    return lb.rematch(premark=True)


@app.post("/api/rescan")
def api_rescan():
    lib().start_scan()
    return scan_status()


@app.get("/api/pick-folder")
def pick_folder():
    """Нативный диалог выбора папки (в отдельном процессе, чтобы не мешать серверу)."""
    code = (
        "import tkinter as tk, tkinter.filedialog as fd\n"
        "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
        "print(fd.askdirectory(title='Выберите папку с фото и видео', mustexist=True) or '')\n"
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, env=env, timeout=600)
    path = r.stdout.decode("utf-8", "ignore").strip()
    return {"path": str(Path(path)) if path else None}


@app.get("/api/items")
def items():
    # json.dumps напрямую: стандартная сериализация FastAPI на десятках тысяч записей в разы медленнее
    return Response(json.dumps(lib().list_items(), ensure_ascii=False), media_type="application/json")


@app.get("/api/item/{mid}")
def item(mid: int):
    d = lib().item(mid)
    if not d:
        raise HTTPException(404)
    return d


# Вместо 404 (браузер пишет каждую в консоль): пустая плитка. Не кэшируется — миниатюра может появиться позже.
NO_THUMB = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>'


@app.get("/api/thumb/{mid}")
def thumb(mid: int):
    data = lib().thumb(mid)
    if data is None:
        return Response(NO_THUMB, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=31536000, immutable"})


@app.get("/api/file/{mid}")
def file(mid: int):
    try:
        res = lib().file_for_view(mid)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Не удалось открыть файл: {e}") from e
    if not res or not res[0].exists():
        raise HTTPException(404)
    path, _ = res
    return FileResponse(path, headers={"Cache-Control": "max-age=3600"})


@app.post("/api/status")
def set_status(req: StatusReq):
    if req.status not in (0, 1, 2):
        raise HTTPException(400)
    lib().set_status(req.ids, req.status)
    return {"ok": True}


@app.post("/api/settings")
def set_settings(req: SettingsReq):
    lib().save_settings(req.model_dump())
    lib().regroup()
    return {"settings": lib().settings().__dict__}


class EmptyReq(BaseModel):
    phone: bool = False


@app.post("/api/trash/empty")
def empty_trash(req: EmptyReq | None = None):
    try:
        return lib().empty_trash(phone=bool(req and req.phone))
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e


@app.post("/api/reveal/{mid}")
def reveal(mid: int):
    p = lib().abs_path(mid)
    if p:
        trash.reveal(p)
    return {"ok": True}


@app.post("/api/open-external/{mid}")
def open_external(mid: int):
    p = lib().abs_path(mid)
    if p:
        trash.open_external(p)
    return {"ok": True}
