"""A read-only check of what the installed Dahua NetSDK Python wrapper can read from a recorder: disk state, recording state and the recorder's clock.

Run it from a folder that is not the wrapper's own, with the same Python the gateway uses:
    cd /tmp && /opt/gateinsight/backend/.venv/bin/python -m dahua.probe            (what the kit offers; no recorder is contacted)
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
    if len(argv) >= 5:
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
