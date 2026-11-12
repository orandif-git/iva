"""A read-only check of what the installed Dahua NetSDK Python wrapper can read from a recorder: disk state, recording state and the recorder's clock.

Run it from a folder that is not the wrapper's own, with the same Python the gateway uses:
    cd /tmp && PYTHONPATH=/www/wwwroot/iva/backend /www/wwwroot/iva/backend/.venv/bin/python -m dahua.probe            (what the kit offers; no recorder is contacted)
    ... -m dahua.probe --detail          (also the exact calls, structure fields and state types)
    ... -m dahua.probe --events          (what the kit offers for alarms, restarts, heat and the state of each camera)
    cd /tmp && /opt/gateinsight/backend/.venv/bin/python -m dahua.probe IP PORT USER PASSWORD     (also asks one recorder, if it can be reached directly)
It changes nothing and prints no password. Send its whole output back."""
import sys


def names(mod, *words):
    return sorted(n for n in dir(mod) if any(w.lower() in n.lower() for w in words) and not n.startswith("__"))


def main(argv) -> int:
    try:
        from NetSDK import NetSDK, SDK_Enum, SDK_Struct
    except ImportError as e:
        print("The Dahua NetSDK wrapper is not installed for this Python:", e)
        return 1
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
