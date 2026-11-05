"""One-off check of Dahua "Auto Register" with a real recorder (run on the server, with Dahua's NetSDK Python wrapper installed).

What it does, in order:
  1. listens on a port (default 7000) for a Dahua recorder to register with this server;
  2. when one does, logs into it over that registered connection with the user name and password you give;
  3. starts one channel's live video and writes about 10 seconds of the raw stream into a file;
  4. prints what arrived (data types, sizes, the first bytes), then logs out and stops.

It changes nothing on the recorder. Use a viewer-only account made for this test. The password is read from the
environment (DAHUA_PASS), never from the command line, so it does not show in the process list.

  DAHUA_PASS='the-test-password' /root/dhvenv/bin/python backend/scripts/dahua_probe.py --user viewer --wait 180

The NetSDK wrapper (a .whl file from Dahua's own download) must be installed in the Python you run this with.
Dahua's libraries are not part of this repository and must come from Dahua's own download.
"""
import argparse
import os
import queue
import sys
import time
from ctypes import POINTER, c_ubyte, c_ushort, c_void_p, cast, create_string_buffer, sizeof, string_at

try:
    from NetSDK.NetSDK import NetClient
    from NetSDK.SDK_Callback import fDisConnect, fHaveReConnect, fRealDataCallBackEx2, fServiceCallBack
    from NetSDK.SDK_Enum import EM_LOGIN_SPAC_CAP_TYPE, EM_REALDATA_FLAG, SDK_RealPlayType
    from NetSDK.SDK_Struct import NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY, NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY
except ImportError as e:
    sys.exit(f"The Dahua NetSDK wrapper is not installed for this Python ({e}). Install the .whl from Dahua's download, "
             "and run this from a folder that is not the wrapper's own folder.")

NET_DVR_DISCONNECT, NET_DVR_SERIAL_RETURN = -1, 1          # the two callback commands the SDK uses while listening


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=7000, help="port to listen on (default 7000)")
    p.add_argument("--user", required=True, help="a viewer-only user made on the recorder for this test")
    p.add_argument("--channel", type=int, default=0, help="channel number as the SDK counts it: 0 is the first channel")
    p.add_argument("--main-stream", action="store_true", help="read the main stream instead of the lighter sub stream")
    p.add_argument("--seconds", type=int, default=10, help="how much live video to read")
    p.add_argument("--wait", type=int, default=180, help="how long to wait for the recorder to register")
    p.add_argument("--out", default="/root/dahua_test.raw", help="file for the raw video")
    p.add_argument("--only-id", help="accept only this Sub-Device ID (ignore other devices that register)")
    a = p.parse_args()
    password = os.environ.get("DAHUA_PASS")
    if not password:
        sys.exit("Set the password in the environment first:  export DAHUA_PASS='...'  (it is never taken from the command line)")

    registrations: "queue.Queue[tuple]" = queue.Queue()
    stats = {"types": {}, "bytes": 0, "first": b"", "chunks": 0}
    out = open(a.out, "wb")

    def on_disconnect(login_id, ip, port, user):
        print("recorder disconnected:", ip.decode(errors="replace") if ip else "", port)

    def on_reconnect(login_id, ip, port, user):
        print("recorder reconnected")

    def on_service(handle, ip, port, command, param, length, user):
        """Called by the SDK when a recorder connects. Only note it here; logging in happens outside the callback."""
        ident = ""
        if param and length:
            ident = string_at(param, length).decode("gbk", "replace").strip("\x00 ").strip()
        print(f"[{time.strftime('%H:%M:%S')}] registration event: command={command} from {ip.decode(errors='replace')}:{port} id={ident!r}")
        if command == NET_DVR_SERIAL_RETURN:
            registrations.put((ip.decode(), int(port), ident))
        return 0

    def on_data(real_handle, data_type, buffer, size, param, user):
        stats["types"][data_type] = stats["types"].get(data_type, 0) + size
        if data_type == 0 and size:
            raw = bytes(cast(buffer, POINTER(c_ubyte * size)).contents)
            if not stats["first"]:
                stats["first"] = raw[:16]
            stats["bytes"] += size
            stats["chunks"] += 1
            out.write(raw)

    # The SDK keeps pointers to these callbacks, so they must stay alive for the whole run.
    cb_disc, cb_recon, cb_service, cb_data = fDisConnect(on_disconnect), fHaveReConnect(on_reconnect), fServiceCallBack(on_service), fRealDataCallBackEx2(on_data)

    sdk = NetClient()
    sdk.InitEx(cb_disc)
    sdk.SetAutoReconnect(cb_recon)
    server = sdk.ListenServer("0.0.0.0", c_ushort(a.port), 1000, cb_service, 0)
    if not server:
        print("could not start listening (is another program using the port? is an old listener still running?)")
        sdk.Cleanup()
        return 1
    print(f"listening on port {a.port}; waiting up to {a.wait} s for a recorder to register ...")
    login_id = play_id = 0
    try:
        deadline = time.time() + a.wait
        reg = None
        while time.time() < deadline and reg is None:
            try:
                cand = registrations.get(timeout=2)
            except queue.Empty:
                continue
            if a.only_id and cand[2] != a.only_id:
                print("ignoring a different device:", cand[2])
                continue
            reg = cand
        if reg is None:
            print("no recorder registered in time")
            return 2
        ip, port, ident = reg
        print(f"logging in to {ident!r} at {ip}:{port} ...")
        time.sleep(0.5)
        id_buf = create_string_buffer(ident.encode("gbk"))        # the registering device's id, as the SDK expects it
        s_in = NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY()
        s_in.dwSize = sizeof(NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY)
        s_in.szIP = ip.encode()
        s_in.nPort = port
        s_in.szUserName = a.user.encode()
        s_in.szPassword = password.encode()
        s_in.emSpecCap = EM_LOGIN_SPAC_CAP_TYPE.SERVER_CONN
        s_in.pCapParam = cast(id_buf, c_void_p)
        s_out = NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY()
        s_out.dwSize = sizeof(NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY)
        login_id, info, problem = sdk.LoginWithHighLevelSecurity(s_in, s_out)
        if not login_id:
            print("login failed:", problem or sdk.GetLastErrorMessage())
            return 3
        print(f"login OK. channels on the recorder: {info.nChanNum}")
        kind = SDK_RealPlayType.Realplay if a.main_stream else SDK_RealPlayType.Realplay_1
        play_id = sdk.RealPlayEx(login_id, a.channel, 0, kind)
        if not play_id:
            print("could not start live video:", sdk.GetLastErrorMessage())
            return 4
        sdk.SetRealDataCallBackEx2(play_id, cb_data, None, EM_REALDATA_FLAG.RAW_DATA)
        print(f"reading live video for {a.seconds} s ...")
        time.sleep(a.seconds)
    finally:
        if play_id:
            sdk.StopRealPlayEx(play_id)
        if login_id:
            sdk.Logout(login_id)
        sdk.StopListenServer(server)
        sdk.Cleanup()
        out.close()
    print(f"\nreceived {stats['bytes']} bytes in {stats['chunks']} chunks; by data type: {stats['types']}")
    print("first bytes (hex):", stats["first"].hex(" "), "| as text:", stats["first"].decode("latin-1").replace("\x00", "."))
    print("saved to", a.out)
    return 0 if stats["bytes"] else 5


if __name__ == "__main__":
    sys.exit(main())
