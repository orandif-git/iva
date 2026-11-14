"""The oldest-recording probe: how it words what the recorder's kit returns (the recorder itself is not needed)."""
import ctypes

from dahua import probe


class FakeTime(ctypes.Structure):
    _fields_ = [("dwYear", ctypes.c_int), ("dwMonth", ctypes.c_int), ("dwDay", ctypes.c_int), ("dwHour", ctypes.c_int), ("dwMinute", ctypes.c_int), ("dwSecond", ctypes.c_int)]


class FakeFile(ctypes.Structure):
    _fields_ = [("ch", ctypes.c_int), ("starttime", FakeTime), ("endtime", FakeTime), ("size", ctypes.c_int), ("driveno", ctypes.c_int)]


def a_file(day):
    return FakeFile(2, FakeTime(2026, 10, day, 3, 12, 9), FakeTime(2026, 10, day, 4, 0, 0), 5000, 0)


def test_a_recording_is_described_by_its_times_size_and_channel():
    text = probe.describe_file(a_file(31))
    assert "starttime=2026-10-31 03:12:09" in text and "endtime=2026-10-31 04:00:00" in text and "size=5000" in text and "ch=2" in text


def test_an_answer_with_files_shows_the_first_and_the_last():
    lines = probe.describe_result((True, [a_file(31), a_file(5), a_file(14)]))
    joined = "\n".join(lines)
    assert "a list of 3 recordings" in joined and "first: ch=2, starttime=2026-10-31" in joined and "last:  ch=2, starttime=2026-10-14" in joined


def test_an_answer_with_only_a_count_says_so():
    lines = probe.describe_result((True, 120))
    assert any("part 1: int 120" in x for x in lines), lines


def test_an_odd_answer_does_not_crash_the_probe():
    assert probe.describe_result(None)[0].startswith("answer is a NoneType")
    assert probe.describe_file("odd") == "'odd'"


def test_the_block_the_wrapper_returns_is_opened_and_only_the_real_entries_count():
    block = (FakeFile * 5000)()                                 # a recorder answers (ok, how many, a block of up to 5000 entries)
    block[0], block[1], block[2] = a_file(20), a_file(3), a_file(27)      # not in time order
    lines = probe.describe_result((1, 3, block))
    joined = "\n".join(lines)
    assert "a list of 3 recordings" in joined, joined
    assert "earliest start: 2026-10-03 03:12:09, latest start: 2026-10-27 03:12:09" in joined, joined
