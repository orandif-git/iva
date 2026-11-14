"""A read-only check of what the installed Dahua NetSDK Python wrapper can read from a recorder: disk state, recording state and the recorder's clock.

Run it from a folder that is not the wrapper's own, with the same Python the gateway uses:
    cd /tmp && PYTHONPATH=/www/wwwroot/iva/backend /www/wwwroot/iva/backend/.venv/bin/python -m dahua.probe            (what the kit offers; no recorder is contacted)
    ... -m dahua.probe --detail          (also the exact calls, structure fields and state types)
    ... -m dahua.probe --codes           (the number codes and structure layouts for alarm listening, the device log and hardware status)\n    ... -m dahua.probe --events          (what the kit offers for alarms, restarts, heat and the state of each camera)
    cd /tmp && /opt/gateinsight/backend/.venv/bin/python -m dahua.probe IP PORT USER PASSWORD     (also asks one recorder, if it can be reached directly)
    ... -m dahua.probe --oldest IP PORT USER PASSWORD [CHANNELS]     (asks one recorder for its oldest recording on its first channels: read-only)
It changes nothing and prints no password. Send its whole output back."""
import sys


def names(mod, *words):
    return sorted(n for n in dir(mod) if any(w.lower() in n.lower() for w in words) and not n.startswith("__"))


def _time_text(t) -> str:
    """A NET_TIME-like structure as 2026-11-14 03:12:09, or None when it has no year."""
    try:
        return f"{t.dwYear:04d}-{t.dwMonth:02d}-{t.dwDay:02d} {t.dwHour:02d}:{t.dwMinute:02d}:{t.dwSecond:02d}"
    except Exception:
        return None


def describe_file(f) -> str:
    """One recording as the kit returned it: every field that is about time, and its size and channel when it has them. Falls back to a short text for anything else."""
    fields = getattr(f, "_fields_", None)
    if not fields:
        return repr(f)[:160]
    out = []
    for name, _typ in fields:
        v = getattr(f, name, None)
        low = name.lower()
        if "time" in low or "start" in low or "end" in low:
            text = _time_text(v) if hasattr(v, "dwYear") else (v if isinstance(v, (int, float, str)) else None)
            if text is not None:
                out.append(f"{name}={text}")
        elif low in ("ch", "nchn", "channel", "size", "length", "driveno"):
            out.append(f"{name}={v}")
    return ", ".join(out) or repr(f)[:160]


def describe_result(res) -> list[str]:
    """What QueryRecordFile gave back, in words: the shape of the answer, how many recordings, and the first and last one."""
    lines = [f"answer is a {type(res).__name__}" + (f" of {len(res)} parts" if isinstance(res, (tuple, list)) else "")]
    if not isinstance(res, tuple):
        return lines + [f"   value: {repr(res)[:160]}"]
    for n, part in enumerate(res):
        if isinstance(part, (list, tuple)):
            lines.append(f"   part {n}: a list of {len(part)} recordings")
            if part:
                lines.append(f"      first: {describe_file(part[0])}")
                if len(part) > 1:
                    lines.append(f"      last:  {describe_file(part[-1])}")
        else:
            lines.append(f"   part {n}: {type(part).__name__} {repr(part)[:100]}")
    return lines


def _login(NetSDK, ip, port, user, pw):
    c = NetSDK.NetClient()
    c.InitEx(None)
    from NetSDK.SDK_Struct import NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY, NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY
    from ctypes import sizeof
    i, o = NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY(), NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY()
    i.dwSize, o.dwSize = sizeof(i), sizeof(o)
    i.szIP, i.nPort, i.szUserName, i.szPassword = ip.encode(), port, user.encode(), pw.encode()
    lid, info, err = c.LoginWithHighLevelSecurity(i, o)
    return c, lid, err


def oldest(NetSDK, argv) -> int:
    """Read-only. Asks one recorder for the recordings of a very wide period and shows exactly what comes back, so we can see whether the oldest recording's time can be read from it,
    and how wide a period the recorder accepts. Nothing on the recorder is changed."""
    import time
    from datetime import datetime, timedelta
    from NetSDK.SDK_Struct import NET_TIME
    rest = [a for a in argv if a != "--oldest"][1:]
    if len(rest) < 4:
        print("Usage: -m dahua.probe --oldest IP PORT USER PASSWORD [CHANNELS]")
        return 2
    ip, port, user, pw = rest[0], int(rest[1]), rest[2], rest[3]
    channels = int(rest[4]) if len(rest) > 4 else 2
    c, lid, err = _login(NetSDK, ip, port, user, pw)
    print("sign-in:", "ok" if lid else f"refused ({err})")
    if not lid:
        return 1
    try:
        t = NET_TIME()
        if not c.QueryDeviceTimeEx(lid, t, 3000):
            print("could not read the recorder's clock")
            return 1
        end = datetime(t.dwYear, t.dwMonth, t.dwDay, t.dwHour, t.dwMinute, t.dwSecond)
        print("recorder clock:", end.isoformat())

        def stamp(d):
            x = NET_TIME()
            x.dwYear, x.dwMonth, x.dwDay, x.dwHour, x.dwMinute, x.dwSecond = d.year, d.month, d.day, d.hour, d.minute, d.second
            return x

        for index in range(max(1, channels)):
            print(f"\n== channel {index + 1}")
            for label, start in (("since 2000", datetime(2000, 1, 1)), ("last 365 days", end - timedelta(days=365)), ("last 90 days", end - timedelta(days=90)), ("last 30 days", end - timedelta(days=30))):
                t0 = time.monotonic()
                try:
                    res = c.QueryRecordFile(lid, index, 0, stamp(start), stamp(end), "", 5000, False)
                except Exception as e:
                    print(f"-- {label}: failed, {type(e).__name__}: {e}")
                    continue
                print(f"-- {label}: asked from {start.date()} to {end.date()}, answered in {time.monotonic() - t0:.1f} s")
                for line in describe_result(res):
                    print("  ", line)
    finally:
        try:
            c.Logout(lid)
        except Exception:
            pass
    return 0


def main(argv) -> int:
    try:
        from NetSDK import NetSDK, SDK_Enum, SDK_Struct
    except ImportError as e:
        print("The Dahua NetSDK wrapper is not installed for this Python:", e)
        return 1
    if "--oldest" in argv:
        return oldest(NetSDK, argv)
    print("== Methods on NetClient that read device state, config or time")
    for n in names(NetSDK.NetClient, "QueryDev", "GetDevConfig", "GetNewDevConfig", "QueryDeviceTime", "GetDeviceTime", "QueryRecord", "QuerySystemInfo", "GetDevState", "QueryDevInfo", "StorageDevice", "HardDisk"):
        print("  ", n)
    print("== Structures about disks, recording and time")
    for n in names(SDK_Struct, "HARDDISK", "DISK", "STORAGE", "RECORD_STATE", "RECORDSTATE", "DEVICE_TIME", "DEV_TIME", "NET_TIME", "WORKSTATE"):
        print("  ", n)
    print("== Enumerations about device state")
    for n in names(SDK_Enum, "DEVSTATE", "DEV_STATE", "DISK", "RECORD", "SYSTEM_INFO"):
        print("  ", n)
    if "--events" in argv:
        import inspect
        from NetSDK import SDK_Callback
        print("== What the kit offers for listening to a recorder's own alarms")
        for n in names(NetSDK.NetClient, "Listen", "MessCallBack", "SetDVRMess", "Subscribe", "Attach", "Alarm", "Event"):
            f = getattr(NetSDK.NetClient, n, None)
            try:
                print(f"   {n}{inspect.signature(f)}")
            except Exception:
                print(f"   {n}")
        print("== Callback types")
        for n in names(SDK_Callback, "Mess", "Alarm", "Event"):
            print("  ", n)
        print("== Alarm and event kinds about disks, video, restarts, heat")
        for mod in (SDK_Enum, SDK_Struct):
            for n in names(mod, "STORAGE", "DISK", "VIDEOLOSS", "VIDEO_LOSS", "VIDEOBLIND", "BLIND", "TAMPER", "REBOOT", "POWER", "TEMPERATURE", "FAN", "POE", "SMART", "HEALTH"):
                print("  ", mod.__name__.split(".")[-1], n)
        print("== Calls that might tell the state of each camera or the device log")
        for n in names(NetSDK.NetClient, "RemoteDevice", "Digital", "Channel", "DeviceLog", "RunTime", "WorkState", "SysInfo", "SystemInfo"):
            f = getattr(NetSDK.NetClient, n, None)
            try:
                print(f"   {n}{inspect.signature(f)}")
            except Exception:
                print(f"   {n}")
        print("== Structures about remote (network) cameras, logs, or work state")
        for n in names(SDK_Struct, "REMOTE_DEVICE", "DIGITAL_CHANNEL", "CHANNEL_STATE", "DEVICE_LOG", "QUERY_LOG", "WORKSTATE", "RUN_TIME", "UPTIME"):
            print("  ", n)
    if "--codes" in argv:
        import inspect
        import pkgutil
        import NetSDK as pkg
        mods = {m.name: __import__(m.name, fromlist=["x"]) for m in pkgutil.iter_modules(pkg.__path__, "NetSDK.")}
        print("== Modules of the kit:", ", ".join(sorted(mods)))
        words = ("STORAGE", "HDD", "DISK", "FRONTDISCONNECT", "FRONTDISCONNET", "VIDEOBLIND", "VIDEOLOST", "VIDEO_LOSS", "SHELTER", "POWERFAULT", "OVER_TEMPERATURE", "FAN", "REBOOT", "DEVICE_LOG", "ALARM_RECORD")
        print("== Number codes for the alarms we care about (name = value in hex)")
        seen = set()
        for mn, m in sorted(mods.items()):
            for n in dir(m):
                v = getattr(m, n, None)
                if n.startswith("_") or not any(w in n.upper() for w in words):
                    continue
                if isinstance(v, int) and not isinstance(v, bool) and not isinstance(v, type):
                    key = (n, v)
                    if key not in seen:
                        seen.add(key)
                        print(f"   {mn.split('.')[-1]}.{n} = {v:#x}")
                elif isinstance(v, type) and hasattr(v, "__members__"):
                    for mem, val in v.__members__.items():
                        if any(w in mem.upper() for w in words) and (mem, int(val)) not in seen:
                            seen.add((mem, int(val)))
                            print(f"   {n}.{mem} = {int(val):#x}")
        print("== The alarm callback")
        cb = getattr(mods["NetSDK.SDK_Callback"], "fMessCallBackEx1", None)
        print("  ", getattr(cb, "_argtypes_", None), getattr(cb, "_restype_", None))
        print("== Structure fields")
        for n in ("NET_CLIENT_ALARM_STATE", "ALARM_HDD_HEALTHALARM_INFO", "ALARM_HDD_TEMPERATUREALARM_INFO", "ALARM_STORAGE_FAILURE_EX", "ALARM_STORAGE_LOW_SPACE_INFO", "ALARM_FRONTDISCONNET_INFO",
                  "NET_A_ALARM_VIDEO_LOSS_INFO", "NET_A_DEV_EVENT_ALARM_VIDEOBLIND", "NET_ALARM_OVER_TEMPERATURE_INFO", "NET_A_ALARM_POWERFAULT_INFO", "NET_HDDSMART_INFO", "SDK_FAN_STATUS", "SDK_POWER_STATUS",
                  "SDK_TEMPERATURE_STATUS", "NET_A_QUERY_DEVICE_LOG_PARAM", "NET_A_DEVICE_LOG_ITEM", "NET_A_REMOTE_DEVICE", "NET_REMOTE_DEVICE_EX", "NET_IN_GET_CHANNEL_INFO", "NET_OUT_GET_CHANNEL_INFO"):
            st = getattr(SDK_Struct, n, None)
            fields = [f[0] + ":" + getattr(f[1], "__name__", str(f[1])) for f in getattr(st, "_fields_", [])]
            print(f"   {n}: {', '.join(fields)[:700] if fields else '(not found)'}")
        print("== Meanings of storage states")
        for n in ("EM_STORAGE_HEALTH_TYPE", "EM_STORAGE_DEVICE_STATUS", "EM_STORAGE_ERROR"):
            e = getattr(SDK_Enum, n, None)
            print(f"   {n}:", {k: int(v) for k, v in getattr(e, "__members__", {}).items()})
        print("== Call signatures")
        for n in ("StartListenEx", "SetDVRMessCallBackEx1", "QueryDeviceLog", "GetChannelInfo", "QueryNewSystemInfo"):
            try:
                print(f"   {n}{inspect.signature(getattr(NetSDK.NetClient, n))}")
            except Exception as e:
                print(f"   {n}: {type(e).__name__}")
    if "--detail" in argv:
        import inspect
        print("== Call signatures")
        for n in ("GetStorageDeviceInfo", "QueryDevState", "QueryDeviceTimeEx", "QueryDeviceUTC", "QueryRecordFile", "GetDevConfig", "GetNewDevConfig", "QueryDevInfo"):
            f = getattr(NetSDK.NetClient, n, None)
            try:
                print(f"   {n}{inspect.signature(f)}")
            except Exception as e:
                print(f"   {n}: {type(e).__name__}")
            doc = (inspect.getdoc(f) or "").strip().splitlines()
            if doc:
                print("      ", doc[0][:160])
        print("== Structure fields")
        for n in ("NET_DEVICE_STORAGE_INFO", "NET_DEV_DISKSTATE", "NET_STORAGE_PARTITION_INFO", "SDK_HARDDISK_STATE", "NET_IN_STORAGE_DEV_INFOS", "NET_OUT_STORAGE_DEV_INFOS",
                  "NET_TIME_EX", "NET_TIME", "ALARM_STORAGE_FAILURE", "ALARM_STORAGE_LOW_SPACE_INFO"):
            st = getattr(SDK_Struct, n, None)
            fields = [f[0] + ":" + getattr(f[1], "__name__", str(f[1])) for f in getattr(st, "_fields_", [])]
            print(f"   {n}: {', '.join(fields)[:600]}")
        print("== Members of EM_QUERY_DEV_STATE_TYPE")
        for m in dir(SDK_Enum.EM_QUERY_DEV_STATE_TYPE):
            if not m.startswith("_"):
                print("  ", m, getattr(SDK_Enum.EM_QUERY_DEV_STATE_TYPE, m, ""))
        print("== Members of EM_QUERY_RECORD_TYPE")
        for m in dir(SDK_Enum.EM_QUERY_RECORD_TYPE):
            if not m.startswith("_"):
                print("  ", m, getattr(SDK_Enum.EM_QUERY_RECORD_TYPE, m, ""))
    if len(argv) >= 5 and "--detail" not in argv[1:2]:
        ip, port, user, pw = argv[1], int(argv[2]), argv[3], argv[4]
        print("== Asking one recorder")
        try:
            c = NetSDK.NetClient()
            c.InitEx(None)
            from NetSDK.SDK_Struct import NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY, NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY
            from ctypes import sizeof
            i, o = NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY(), NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY()
            i.dwSize, o.dwSize = sizeof(i), sizeof(o)
            i.szIP, i.nPort, i.szUserName, i.szPassword = ip.encode(), port, user.encode(), pw.encode()
            lid, info, err = c.LoginWithHighLevelSecurity(i, o)
            print("   sign-in:", "ok" if lid else f"refused ({err})")
            if lid:
                for n in ("QueryDeviceTime", "GetDeviceTime"):
                    f = getattr(c, n, None)
                    if f:
                        try:
                            print(f"   {n}:", f(lid))
                        except Exception as e:
                            print(f"   {n} failed:", type(e).__name__, e)
                c.Logout(lid)
        except Exception as e:
            print("   could not ask the recorder:", type(e).__name__, e)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
