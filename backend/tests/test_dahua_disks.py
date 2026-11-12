"""The disk reading of the Dahua driver, against a stand-in that behaves like the real wrapper (which makes the pointers itself: passing it a pointer fails)."""
import ctypes
import enum
import sys
import types

import pytest

from dahua.driver import NetSdkDriver


class DiskState(ctypes.Structure):
    _fields_ = [("dwVolume", ctypes.c_uint), ("dwFreeSpace", ctypes.c_uint), ("dwStatus", ctypes.c_ubyte), ("bDiskNum", ctypes.c_ubyte), ("bSubareaNum", ctypes.c_ubyte), ("bSignal", ctypes.c_ubyte)]


class HardDisks(ctypes.Structure):
    _fields_ = [("dwDiskNum", ctypes.c_uint), ("stDisks", DiskState * 256)]


class StateType(enum.IntEnum):
    DISK = 4


@pytest.fixture
def driver(monkeypatch):
    enum_mod, struct_mod, pkg = types.ModuleType("NetSDK.SDK_Enum"), types.ModuleType("NetSDK.SDK_Struct"), types.ModuleType("NetSDK")
    enum_mod.EM_QUERY_DEV_STATE_TYPE, struct_mod.SDK_HARDDISK_STATE = StateType, HardDisks
    for name, mod in (("NetSDK", pkg), ("NetSDK.SDK_Enum", enum_mod), ("NetSDK.SDK_Struct", struct_mod)):
        monkeypatch.setitem(sys.modules, name, mod)
    d = NetSdkDriver.__new__(NetSdkDriver)

    class Sdk:
        answer = True
        def QueryDevState(self, login_id, kind, buf, size, ret_len, wait):
            ctypes.c_int(ret_len)                         # the real wrapper makes a number of the length itself: given a ctypes number it raises "'c_int' object cannot be interpreted as an integer"
            pointer = ctypes.pointer(buf)                 # and a pointer of the buffer: a pointer to a pointer (or to a byref) would raise "_type_ must have storage info"
            assert kind == 4 and size == ctypes.sizeof(HardDisks)
            pointer.contents.dwDiskNum = 2
            pointer.contents.stDisks[0] = DiskState(1_000_000, 250_000, 1, 0, 1, 0)
            pointer.contents.stDisks[1] = DiskState(2_000_000, 0, 2, 1, 1, 0)       # the second disk is faulty
            return self.answer

    d._sdk = Sdk()
    return d


def test_the_disks_are_read_with_their_sizes_free_space_and_state(driver):
    assert driver._disks(7) == [{"num": 0, "total_mb": 1_000_000, "free_mb": 250_000, "status": 1, "state": 1}, {"num": 1, "total_mb": 2_000_000, "free_mb": 0, "status": 2, "state": 2}]


def test_a_disk_state_carries_the_disk_type_in_its_high_bits_and_the_state_in_its_low_bits(driver):
    driver._sdk.QueryDevState = lambda *a: (a[2].__setattr__("dwDiskNum", 1), a[2].stDisks.__setitem__(0, DiskState(10, 5, 0x12, 3, 1, 0)), (True, 4))[2]    # a tuple answer, status 0x12
    (d,) = driver._disks(7)
    assert d["status"] == 0x12 and d["state"] == 2 and d["num"] == 3, "0x12 is a read-only disk (high bits) that is faulty (low bits)"


def test_a_recorder_that_cannot_be_asked_gives_none_and_not_a_fault(driver):
    driver._sdk.answer = False
    assert driver._disks(7) is None


def test_how_many_days_of_video_a_recorder_holds_is_found_in_steps_and_remembered(driver, monkeypatch):
    class Time(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint) for n in ("dwYear", "dwMonth", "dwDay", "dwHour", "dwMinute", "dwSecond")]
    structs = sys.modules["NetSDK.SDK_Struct"]
    structs.NET_TIME = Time
    asked = []

    def query(login, index, kind, start, end, card, wait, by_time):
        asked.append((start.dwYear, start.dwMonth, start.dwDay))
        from datetime import date
        age = (date(2026, 11, 12) - date(start.dwYear, start.dwMonth, start.dwDay)).days
        return (True, [object()] * 3) if age <= 25 else (True, [])                 # the recorder holds 25 days: footage 21 days ago, none 30 days ago

    driver._sdk.QueryRecordFile = query
    driver._days = {}
    clock = {"utc": None, "local": "2026-11-12T08:00:00"}
    rec = {"1": {"recent": 3, "day": 20}, "2": {"recent": None, "day": None}}
    assert driver._days_held(7, clock, rec) == 21, "found in steps: 21 days has video, 30 has none"
    n = len(asked)
    assert driver._days_held(7, clock, rec) == 21 and len(asked) == n, "asked again only after hours, not every ten minutes"
    assert driver._days_held(8, clock, {"1": {"recent": None, "day": None}}) is None, "no channel that records: no answer"
    assert driver._days_held(9, None, rec) is None


def test_the_survey_writes_the_raw_answers_to_the_log_and_never_raises(driver, monkeypatch, caplog):
    import logging
    enum_mod, struct_mod = sys.modules["NetSDK.SDK_Enum"], sys.modules["NetSDK.SDK_Struct"]

    class Kinds(enum.IntEnum):
        DISK = 4
        ONLINE = 53
        SOFTWARE = 15
    enum_mod.EM_QUERY_DEV_STATE_TYPE = Kinds

    class Storage(ctypes.Structure):
        _fields_ = [("szName", ctypes.c_char * 8), ("fLifePercent", ctypes.c_float)]
    struct_mod.SDK_STORAGE_DEVICE = Storage

    def query(login, kind, buf, size, got, wait):
        ctypes.c_int(got)                                # as the real wrapper does
        if kind == 15:
            raise TypeError("a call this recorder does not support")
        buf.value = b"\x01\x00"
        return True

    def storage(login, name, dev, wait):
        dev.szName, dev.fLifePercent = b"sda", 87.5
        return 1

    driver._sdk.QueryDevState, driver._sdk.GetStorageDeviceInfo = query, storage
    with caplog.at_level(logging.INFO, logger="dahua.driver"):
        driver.survey(7, 4)
    text = caplog.text
    assert "ONLINE (53) -> result True, 1 bytes in use" in text and "SOFTWARE could not be asked: TypeError" in text
    assert "'szName': 'sda'" in text and "'fLifePercent': 87.5" in text


def test_the_recording_state_of_each_channel_is_read_from_one_byte_each(driver, monkeypatch):
    enum_mod = sys.modules["NetSDK.SDK_Enum"]

    class Kinds(enum.IntEnum):
        RECORDING = 3
    enum_mod.EM_QUERY_DEV_STATE_TYPE = Kinds

    def query(login, kind, buf, size, got, wait):
        ctypes.c_int(got)
        assert kind == 3
        buf.raw = bytes([1, 1, 0, 1]) + bytes(4092)
        return 1

    driver._sdk.QueryDevState = query
    assert driver._recording_now(7, 6) == {"1": True, "2": True, "3": False, "4": True, "5": False, "6": False}


def test_each_disk_gets_its_make_and_serial_number_when_the_recorder_names_its_disks(driver, monkeypatch):
    class Storage(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_uint), ("szName", ctypes.c_char * 16), ("szModule", ctypes.c_char * 40), ("szSerial", ctypes.c_char * 40)]
    sys.modules["NetSDK.SDK_Struct"].SDK_STORAGE_DEVICE = Storage
    asked = []

    def info(login, name, dev, wait):
        asked.append((name, dev.dwSize))
        if name != b"/dev/sda":
            return 0
        dev.szName, dev.szModule, dev.szSerial = b"/dev/sda", b"WDCWD43PURZ", b"WX72D43MN17T"
        return 1

    driver._sdk.GetStorageDeviceInfo = info
    driver._sdk.QueryDevState = lambda *a: (a[2].__setattr__("dwDiskNum", 1), a[2].stDisks.__setitem__(0, DiskState(4_000_000, 1_000_000, 1, 0, 1, 0)), True)[2]
    (d,) = driver._disks(7)
    assert d["model"] == "WDCWD43PURZ" and d["serial"] == "WX72D43MN17T" and d["total_mb"] == 4_000_000
    assert all(size > 0 for _, size in asked), "the structure carries its own size"
    assert [n for n, _ in asked] == [b"/dev/sda", b"/dev/sdb"], "stops at the first disk that is not there"
