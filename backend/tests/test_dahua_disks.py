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
            pointer = ctypes.pointer(buf)                 # what the real wrapper does: a pointer to a pointer (or to a byref) would raise "_type_ must have storage info"
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
