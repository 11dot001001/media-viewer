"""Windows Portable Devices (MTP): iPhone/Android без буквы диска.

Через Shell (как Проводник) список файлов iPhone читается ~0,1 с на файл — для 30 тыс. снимков это час.
WPD перечисляет объекты мгновенно, а свойства (имя, размер, дату) отдаёт пакетно ~25 мс на файл.
ID объектов на iPhone постоянные, поэтому свойства кэшируются: долго только первое подключение.
"""
from __future__ import annotations

import ctypes
import datetime as dt
import threading
import time

import comtypes
import comtypes.client
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

comtypes.client.GetModule("portabledeviceapi.dll")
comtypes.client.GetModule("portabledevicetypes.dll")
from comtypes.gen import PortableDeviceApiLib as port  # noqa: E402
from comtypes.gen import PortableDeviceTypesLib as types  # noqa: E402

CTX = comtypes.CLSCTX_INPROC_SERVER
_OBJ = GUID("{EF6B490D-5CD8-437A-AFFC-DA8B60EE4A3C}")  # WPD_OBJECT_PROPERTIES_V1
_RES_DEFAULT = GUID("{E81E79BE-34F0-41BF-B53F-F1A06AE87842}")  # WPD_RESOURCE_DEFAULT
_DEV = GUID("{26D4979A-E643-4626-9E2B-736DC0C92FDC}")  # WPD_DEVICE_PROPERTIES_V1
FOLDER_TYPES = {
    str(GUID("{27E2E392-A111-48E0-AB0C-E17705A05F85}")),  # WPD_CONTENT_TYPE_FOLDER
    str(GUID("{99ED0160-17FF-4C44-9D98-1D7A6F941921}")),  # WPD_CONTENT_TYPE_FUNCTIONAL_OBJECT (хранилище)
}
PIDS = {"id": 2, "parent": 3, "name": 4, "ctype": 7, "size": 11, "orig": 12, "created": 18, "modified": 19}


class IPortableDevicePropertiesBulkCallback(IUnknown):
    _iid_ = GUID("{9deacb80-11e8-40e3-a9f3-f557986a7845}")
    _methods_ = [
        COMMETHOD([], HRESULT, "OnStart", (["in"], ctypes.POINTER(GUID), "pContext")),
        COMMETHOD([], HRESULT, "OnProgress", (["in"], ctypes.POINTER(GUID), "pContext"),
                  (["in"], ctypes.POINTER(types.IPortableDeviceValuesCollection), "pResults")),
        COMMETHOD([], HRESULT, "OnEnd", (["in"], ctypes.POINTER(GUID), "pContext"), (["in"], HRESULT, "hrStatus")),
    ]


class IPortableDevicePropertiesBulk(IUnknown):
    _iid_ = GUID("{482b05c0-4056-44ed-9e0f-5e23b009da93}")
    _methods_ = [
        COMMETHOD([], HRESULT, "QueueGetValuesByObjectList",
                  (["in"], ctypes.POINTER(types.IPortableDevicePropVariantCollection), "pObjectIDs"),
                  (["in"], ctypes.POINTER(types.IPortableDeviceKeyCollection), "pKeys"),
                  (["in"], ctypes.POINTER(IPortableDevicePropertiesBulkCallback), "pCallback"),
                  (["out"], ctypes.POINTER(GUID), "pContext")),
        COMMETHOD([], HRESULT, "QueueGetValuesByObjectFormat", (["in"], ctypes.POINTER(GUID), "a"),
                  (["in"], ctypes.c_wchar_p, "b"), (["in"], ctypes.c_ulong, "c"), (["in"], ctypes.POINTER(IUnknown), "d"),
                  (["in"], ctypes.POINTER(IUnknown), "e"), (["out"], ctypes.POINTER(GUID), "f")),
        COMMETHOD([], HRESULT, "QueueSetValuesByObjectList", (["in"], ctypes.POINTER(IUnknown), "a"),
                  (["in"], ctypes.POINTER(IUnknown), "b"), (["out"], ctypes.POINTER(GUID), "c")),
        COMMETHOD([], HRESULT, "Start", (["in"], ctypes.POINTER(GUID), "pContext")),
        COMMETHOD([], HRESULT, "Cancel", (["in"], ctypes.POINTER(GUID), "pContext")),
    ]


def _key(fmtid: GUID, pid: int, mod):
    k = comtypes.pointer(mod._tagpropertykey())
    k.contents.fmtid = fmtid
    k.contents.pid = pid
    return k


TK = {n: _key(_OBJ, p, types) for n, p in PIDS.items()}  # ключи для значений из модуля types
PK = {n: _key(_OBJ, p, port) for n, p in PIDS.items()}  # ключи для значений из модуля port


def _union(pv):
    return getattr(pv, pv._fields_[4][0])


def _ole_date(pv) -> float | None:
    """VT_DATE -> «настенное» время в секундах (как у EXIF в остальной программе)."""
    try:
        if pv.vt != 7:
            return None
        days = _union(pv).date
        d = dt.datetime(1899, 12, 30) + dt.timedelta(days=days)
        return (d - dt.datetime(1970, 1, 1)).total_seconds()
    except Exception:  # noqa: BLE001
        return None


def _pump_until(ev: threading.Event, timeout: float) -> None:
    import pythoncom

    end = time.time() + timeout
    while not ev.is_set() and time.time() < end:
        pythoncom.PumpWaitingMessages()
        ev.wait(0.005)


class _Callback(comtypes.COMObject):
    _com_interfaces_ = [IPortableDevicePropertiesBulkCallback]

    def __init__(self, sink):
        super().__init__()
        self.sink = sink
        self.done = threading.Event()
        self.hr = None

    def OnStart(self, pContext):  # noqa: N802
        return 0

    def OnProgress(self, pContext, pResults):  # noqa: N802
        n = ctypes.c_ulong(0)
        pResults._IPortableDeviceValuesCollection__com_GetCount(ctypes.byref(n))
        for i in range(n.value):
            self.sink(pResults.GetAt(i))
        return 0

    def OnEnd(self, pContext, hrStatus):  # noqa: N802
        self.hr = hrStatus
        self.done.set()
        return 0


def _manager():
    return comtypes.client.CreateObject(port.PortableDeviceManager, clsctx=CTX, interface=port.IPortableDeviceManager)


def list_device_ids() -> list[str]:
    comtypes.CoInitialize()
    mgr = _manager()
    n = ctypes.c_ulong(0)
    mgr.GetDevices(ctypes.POINTER(ctypes.c_wchar_p)(), ctypes.pointer(n))
    if not n.value:
        return []
    ids = (ctypes.c_wchar_p * n.value)()
    mgr.GetDevices(ctypes.cast(ids, ctypes.POINTER(ctypes.c_wchar_p)), ctypes.pointer(n))
    return [ids[i] for i in range(n.value)]


class Connection:
    """Подключение к устройству в одном потоке (COM-объекты WPD к потоку привязаны)."""

    def __init__(self, device_id: str):
        comtypes.CoInitialize()
        client = comtypes.client.CreateObject(types.PortableDeviceValues, clsctx=CTX, interface=types.IPortableDeviceValues)
        self.dev = comtypes.client.CreateObject(port.PortableDevice, clsctx=CTX, interface=port.IPortableDevice)
        self.dev.Open(device_id, client)
        self.content = self.dev.Content()
        self.props = self.content.Properties()
        self.keys = self._keys(PIDS.values())
        try:
            self.bulk = self.props.QueryInterface(IPortableDevicePropertiesBulk)
        except comtypes.COMError:
            self.bulk = None

    @staticmethod
    def _keys(pids, fmtid=_OBJ):
        keys = comtypes.client.CreateObject(types.PortableDeviceKeyCollection, clsctx=CTX,
                                            interface=types.IPortableDeviceKeyCollection)
        for p in pids:
            keys.Add(_key(fmtid, p, types))
        return keys

    def children(self, obj_id: str) -> list[str]:
        en = self.content.EnumObjects(0, obj_id, None)
        out = []
        while True:
            arr = (ctypes.c_wchar_p * 500)()
            fetched = ctypes.c_ulong(0)
            en._IEnumPortableDeviceObjectIDs__com_Next(500, ctypes.cast(arr, ctypes.POINTER(ctypes.c_wchar_p)),
                                                       ctypes.byref(fetched))
            out += [arr[i] for i in range(fetched.value)]
            if fetched.value < 500:
                return out

    @staticmethod
    def _row(v, keys) -> dict:
        def get(fn, k):
            try:
                return fn(keys[k])
            except Exception:  # noqa: BLE001
                return None

        name = get(v.GetStringValue, "orig") or get(v.GetStringValue, "name") or ""
        ctype = get(v.GetGuidValue, "ctype")
        created = get(v.GetValue, "created")
        modified = get(v.GetValue, "modified")
        return {
            "id": get(v.GetStringValue, "id"), "name": name,
            "folder": str(ctype) in FOLDER_TYPES if ctype is not None else False,
            "size": get(v.GetUnsignedLargeIntegerValue, "size") or 0,
            "created": _ole_date(created) if created is not None else None,
            "modified": _ole_date(modified) if modified is not None else None,
        }

    def properties(self, ids: list[str], progress=None) -> dict[str, dict]:
        """Свойства объектов: пакетно, если драйвер умеет, иначе по одному."""
        out: dict[str, dict] = {}
        if not ids:
            return out
        if self.bulk is not None:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                coll = comtypes.client.CreateObject(types.PortableDevicePropVariantCollection, clsctx=CTX,
                                                    interface=types.IPortableDevicePropVariantCollection)
                for oid in chunk:
                    pv = types.tag_inner_PROPVARIANT()
                    pv.vt = 31  # VT_LPWSTR
                    _union(pv).pwszVal = oid
                    coll.Add(pv)

                def sink(v):
                    r = self._row(v, TK)
                    if r["id"]:
                        out[r["id"]] = r
                        if progress:
                            progress(len(out))

                cb = _Callback(sink)
                ctx = self.bulk.QueueGetValuesByObjectList(coll, self.keys, cb)
                self.bulk.Start(ctypes.byref(ctx))
                _pump_until(cb.done, 600)
        for oid in ids:  # то, что не пришло пакетом
            if oid not in out:
                try:
                    r = self._row(self.props.GetValues(oid, self.keys), PK)
                    r["id"] = oid
                    out[oid] = r
                except comtypes.COMError:
                    continue
                if progress:
                    progress(len(out))
        return out

    def device_info(self) -> dict:
        keys = self._keys((2, 9, 12), _DEV)  # FIRMWARE, SERIAL, FRIENDLY_NAME
        try:
            v = self.props.GetValues("DEVICE", keys)
        except comtypes.COMError:
            return {}
        res = {}
        for name, pid in (("serial", 9), ("name", 12)):
            try:
                res[name] = v.GetStringValue(_key(_DEV, pid, port))
            except Exception:  # noqa: BLE001
                pass
        return res

    def read_file(self, obj_id: str, size: int, dest) -> None:
        res = self.content.Transfer()
        bufsize, stream = res.GetStream(obj_id, _key(_RES_DEFAULT, 0, port), 0, 0)
        chunk = max(64 * 1024, min(int(bufsize or 0) or 256 * 1024, 1024 * 1024))
        total = 0
        tmp = dest.with_name(dest.name + ".part")
        with open(tmp, "wb") as fh:
            while total < size:
                data, n = stream.RemoteRead(min(chunk, size - total))
                if not n:
                    break
                fh.write(bytes(data[:n]))
                total += n
        if total != size:
            tmp.unlink(missing_ok=True)
            raise OSError(f"скопировано {total} из {size} байт")
        tmp.replace(dest)

    def delete(self, obj_ids: list[str]) -> None:
        coll = comtypes.client.CreateObject(types.PortableDevicePropVariantCollection, clsctx=CTX,
                                            interface=types.IPortableDevicePropVariantCollection)
        for oid in obj_ids:
            pv = types.tag_inner_PROPVARIANT()
            pv.vt = 31
            _union(pv).pwszVal = oid
            coll.Add(pv)
        pcoll = coll.QueryInterface(port.IPortableDevicePropVariantCollection)
        self.content._IPortableDeviceContent__com_Delete(0, pcoll, None)
