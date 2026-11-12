"""The only file that touches Dahua's NetSDK. Everything else talks to the small `Driver` interface, so it is tested with a stand-in
(tests/fake_dahua.py) and the real libraries are needed only on the server.

Dahua's libraries come from Dahua's own download and are not part of this repository. Install the Python wrapper (.whl) into the venv you
run the gateway with. Its callbacks arrive on SDK threads, so nothing here may block or touch asyncio directly."""
import logging
import sys
from dataclasses import dataclass
from typing import Callable, Protocol

log = logging.getLogger("dahua.driver")

SERIAL_RETURN = 1          # listen-callback command: a recorder registered; the parameter is its Sub-Device ID


@dataclass
class LoginResult:
    login_id: int = 0
    channels: int = 0
    serial: str = ""
    error: str = ""


class Driver(Protocol):
    def start(self, bind: str, port: int, on_register: Callable[[str, int, str], None], on_disconnect: Callable[[int], None]) -> bool: ...
    def login(self, ip: str, port: int, ident: str, user: str, password: str) -> LoginResult: ...
    def play(self, login_id: int, channel_index: int, main: bool, on_data: Callable[[bytes], None]) -> int: ...      # 0 on failure
    def stop_play(self, play_id: int) -> None: ...
    def logout(self, login_id: int) -> None: ...
    def health(self, login_id: int, channels: int) -> dict | None: ...      # disks, clock and recordings; what could not be read is None
    def stop(self) -> None: ...


class NetSdkDriver:
    """Dahua NetSDK through its official Python wrapper."""

    def __init__(self):
        try:
            from NetSDK.NetSDK import NetClient
            from NetSDK.SDK_Callback import fDisConnect, fHaveReConnect, fRealDataCallBackEx2, fServiceCallBack
            from NetSDK.SDK_Enum import EM_LOGIN_SPAC_CAP_TYPE, EM_REALDATA_FLAG, SDK_RealPlayType
            from NetSDK.SDK_Struct import NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY, NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY
        except ImportError as e:
            sys.exit(f"The Dahua NetSDK wrapper is not installed for this Python ({e}). Install the .whl from Dahua's download, "
                     "and run from a folder that is not the wrapper's own folder.")
        self._m = dict(fDisConnect=fDisConnect, fHaveReConnect=fHaveReConnect, fRealDataCallBackEx2=fRealDataCallBackEx2, fServiceCallBack=fServiceCallBack,
                       Cap=EM_LOGIN_SPAC_CAP_TYPE, Flag=EM_REALDATA_FLAG, Play=SDK_RealPlayType,
                       In=NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY, Out=NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY)
        self._sdk = NetClient()
        self._keep: dict = {}          # the SDK holds pointers to callbacks: keep every one alive until it is no longer used
        self._server = 0
        self._reported: set = set()
        self._on_disconnect: Callable[[int], None] = lambda _id: None

    def start(self, bind, port, on_register, on_disconnect) -> bool:
        from ctypes import c_ushort, string_at
        m = self._m
        self._on_disconnect = on_disconnect

        def disc(login_id, ip, p, user):
            self._on_disconnect(int(login_id))

        def service(handle, ip, p, command, param, length, user):
            try:
                if command == SERIAL_RETURN:
                    ident = string_at(param, length).decode("gbk", "replace").strip("\x00 ").strip() if param and length else ""
                    on_register(ip.decode(errors="replace"), int(p), ident)
            except Exception:
                log.exception("registration callback failed")
            return 0

        self._keep["disc"], self._keep["recon"], self._keep["service"] = m["fDisConnect"](disc), m["fHaveReConnect"](lambda *a: None), m["fServiceCallBack"](service)
        self._sdk.InitEx(self._keep["disc"])
        self._sdk.SetAutoReconnect(self._keep["recon"])
        self._server = self._sdk.ListenServer(bind, c_ushort(port), 1000, self._keep["service"], 0)
        return bool(self._server)

    def login(self, ip, port, ident, user, password) -> LoginResult:
        from ctypes import c_void_p, cast, create_string_buffer, sizeof
        m = self._m
        id_buf = create_string_buffer(ident.encode("gbk", "replace"))
        s_in = m["In"]()
        s_in.dwSize = sizeof(m["In"])
        s_in.szIP, s_in.nPort = ip.encode(), port
        s_in.szUserName, s_in.szPassword = user.encode(), password.encode()
        s_in.emSpecCap = m["Cap"].SERVER_CONN
        s_in.pCapParam = cast(id_buf, c_void_p)
        s_out = m["Out"]()
        s_out.dwSize = sizeof(m["Out"])
        login_id, info, problem = self._sdk.LoginWithHighLevelSecurity(s_in, s_out)
        if not login_id:
            return LoginResult(error=str(problem or self._sdk.GetLastErrorMessage() or "sign-in refused"))
        raw = getattr(info, "sSerialNumber", b"")
        serial = raw.decode("ascii", "ignore").strip("\x00 ") if isinstance(raw, (bytes, bytearray)) else ""
        return LoginResult(login_id=int(login_id), channels=int(info.nChanNum), serial=serial)

    def play(self, login_id, channel_index, main, on_data) -> int:
        from ctypes import POINTER, c_ubyte, cast
        m = self._m
        kind = m["Play"].Realplay if main else m["Play"].Realplay_1

        def data(real_handle, data_type, buffer, size, param, user):
            if data_type == 0 and size:                    # 0 = the recorder's own container (DHAV), exactly what FFmpeg's `-f dhav` reads
                on_data(bytes(cast(buffer, POINTER(c_ubyte * size)).contents))

        cb = m["fRealDataCallBackEx2"](data)
        play_id = self._sdk.RealPlayEx(login_id, channel_index, 0, kind)
        if not play_id:
            return 0
        self._keep[("play", play_id)] = cb
        self._sdk.SetRealDataCallBackEx2(play_id, cb, None, m["Flag"].RAW_DATA)
        return int(play_id)

    def stop_play(self, play_id) -> None:
        self._sdk.StopRealPlayEx(play_id)
        self._keep.pop(("play", play_id), None)

    # ------------------------------------------------------------------ what the recorder says about itself
    def health(self, login_id, channels) -> dict | None:
        """Disks, clock and, for each channel, how many recordings it holds for the last two hours and the last day. Every part is read on its own and a part that cannot
        be read is None, so one unsupported call never hides the others. The first answer from each recorder is written to the log, to check the readings against the
        recorder's own screen."""
        out = {"disks": None, "clock": None, "recording": {}}
        for name, read in (("disks", self._disks), ("clock", self._clock)):
            try:
                out[name] = read(login_id)
            except Exception:
                log.exception("could not read the %s of login %s", name, login_id)
        try:
            out["recording"] = self._recordings(login_id, channels, out["clock"])
        except Exception:
            log.exception("could not read the recordings of login %s", login_id)
        if login_id not in self._reported:
            self._reported.add(login_id)
            log.info("first health reading of login %s: %s", login_id, out)
        return out

    def _disks(self, login_id):
        from ctypes import byref, c_int, sizeof
        from NetSDK.SDK_Enum import EM_QUERY_DEV_STATE_TYPE
        from NetSDK.SDK_Struct import SDK_HARDDISK_STATE
        st, got = SDK_HARDDISK_STATE(), c_int(0)
        if not self._sdk.QueryDevState(login_id, int(EM_QUERY_DEV_STATE_TYPE.DISK), byref(st), sizeof(st), byref(got), 3000):
            return None
        return [{"num": int(d.bDiskNum), "total_mb": int(d.dwVolume), "free_mb": int(d.dwFreeSpace), "status": int(d.dwStatus), "state": int(d.dwStatus) & 0x0F}
                for d in (st.stDisks[i] for i in range(min(int(st.dwDiskNum), 256)))]

    def _clock(self, login_id):
        from NetSDK.SDK_Struct import NET_TIME_EX
        t = NET_TIME_EX()
        if not self._sdk.QueryDeviceTimeEx(login_id, t, 3000):
            return None
        return {"utc": int(t.dwUTC) or None, "local": f"{t.dwYear:04d}-{t.dwMonth:02d}-{t.dwDay:02d}T{t.dwHour:02d}:{t.dwMinute:02d}:{t.dwSecond:02d}"}

    def _recordings(self, login_id, channels, clock):
        """Per channel (numbered from 1): recordings held for the last two hours and the last day. Needs the recorder's own clock to name the period."""
        from datetime import datetime, timedelta
        from NetSDK.SDK_Struct import NET_TIME
        if not clock or not clock.get("local"):
            return {}
        end = datetime.fromisoformat(clock["local"])

        def stamp(d):
            t = NET_TIME()
            t.dwYear, t.dwMonth, t.dwDay, t.dwHour, t.dwMinute, t.dwSecond = d.year, d.month, d.day, d.hour, d.minute, d.second
            return t

        def count(index, since):
            res = self._sdk.QueryRecordFile(login_id, index, 0, stamp(since), stamp(end), "", 3000, False)
            if not isinstance(res, tuple) or not res or not res[0]:
                return None
            for part in res[1:]:                              # the files, or how many there are: the wrapper gives one of them
                if isinstance(part, (list, tuple)):
                    return len(part)
                if isinstance(part, int) and not isinstance(part, bool):
                    return part
            return None

        return {str(i + 1): {"recent": count(i, end - timedelta(hours=2)), "day": count(i, end - timedelta(hours=24))} for i in range(min(channels, 32))}

    def logout(self, login_id) -> None:
        self._sdk.Logout(login_id)

    def stop(self) -> None:
        if self._server:
            self._sdk.StopListenServer(self._server)
            self._server = 0
        self._sdk.Cleanup()
