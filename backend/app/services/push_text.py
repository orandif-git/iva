"""The words of a phone notification: the alert's own name as the title, then the place and one plain fact (a count, a length of time or a clock time) as the text.
No limits and no instructions: the app and the longer messages carry those. Where the number a sentence needs is missing, the alert's own message is used instead."""


def _times(n) -> str:
    return "once" if int(n) == 1 else f"{int(n)} times"


def _duration(minutes) -> str:
    m = int(float(minutes))
    return f"{m / 60:g} hours" if m >= 120 else f"{m} minute{'s' if m != 1 else ''}"


def _plural(n, one: str, many: str) -> str:
    return f"{int(n)} {one if int(n) == 1 else many}"


# rule type -> (where it happened, the sentence). The sentence gets the details and the clock time; a missing detail raises KeyError and the fallback is used.
_TEXT = {
    "crowding": lambda d, c: f"{_plural(d['people'], 'person', 'people')} inside" + (f" for the last {_duration(d['minutes'])}" if d.get("minutes") else " right now") + ".",
    "people_in_area": lambda d, c: f"{_plural(d['people'], 'person', 'people')} in the area right now.",
    "queue_long": lambda d, c: f"{_plural(d['length'], 'person', 'people')} waiting.",
    "phone_use": lambda d, c: f"a phone was seen in use for {int(d['seconds'])} seconds.",
    "blocked_plate": lambda d, c: f"{d['plate']} arrived at {c}.",
    "vehicle_overstay": lambda d, c: f"{d['plate']} parked for {_duration(d['minutes'])}.",
    "low_confidence_plate": lambda d, c: "a number plate could not be read clearly.",
    "after_hours": lambda d, c: f"movement at {c} while closed.",
    "recorder_offline": lambda d, c: f"no signal as of {c}.",
    "camera_offline": lambda d, c: f"no video as of {c}.",
    "connection_unstable": lambda d, c: f"the connection dropped {int(d['drops'])} times in the last hour.",
    "back_to_normal": lambda d, c: f"back online at {c}.",
    "recording_stopped": lambda d, c: f"{_plural(d['cameras'], 'camera is', 'cameras are')} not saving video.",
    "disk_fault": lambda d, c: f"{d['disk']} has stopped working." if d.get("disk") and d["disk"] != "No disk" else "no working disk was found.",
    "disk_wear": lambda d, c: f"{d['disk']} has about {int(d['percent_left'])}% life left.",
    "storage_full": lambda d, c: "the recorder reports no room for new video.",
    "retention_low": lambda d, c: f"{int(d['days'])} days of video kept.",
    "clock_wrong": lambda d, c: f"the clock is {_plural(d['minutes_out'], 'minute', 'minutes')} off.",
    "recorder_restarted": lambda d, c: f"restarted {_times(d['times'])} today." if int(d["times"]) > 1 else "restarted once.",
    "hardware_fault": lambda d, c: f"{d['what']}.",
    "view_changed": lambda d, c: f"the lens is covered as of {c}." if d["what"] == "covered" else f"the view changed at {c}.",
    "picture_poor": lambda d, c: f"the picture is too {d['what']} to see clearly.",
    "counting_stopped": lambda d, c: f"no visitors counted in the last {_plural(d['hours'], 'hour', 'hours')}.",
    "daily_summary": lambda d, c: str(d["summary"]),
}
_NO_PLACE = {"daily_summary"}


def compose(rule_type: str, name: str, camera_name: str, details: dict, clock: str, title: str, body: str) -> tuple[str, str]:
    """(title, text) for the phone. `name` is the alert's catalogue name; `title` and `body` are the alert's own, used when this one has no short form."""
    make = _TEXT.get(rule_type)
    if make is None:
        return title, body
    try:
        fact = make(details, clock)
    except (KeyError, ValueError, TypeError):
        return name, body
    if rule_type in _NO_PLACE:
        return name, fact[:1].upper() + fact[1:]
    place = (details.get("area") if rule_type in ("people_in_area", "queue_long", "phone_use") else None) or details.get("what_recovered") or camera_name or details.get("recorder") or ""
    return name, f"{place}: {fact}" if place else fact[:1].upper() + fact[1:]
