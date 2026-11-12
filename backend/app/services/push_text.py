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
    "hardware_fault": lambda d, c: ", ".join(x for x in ["temperature is above normal" if d["hot"] else "", "a fan has stopped" if d["fan"] else "", "a camera power port has failed" if d["poe"] else ""] if x) + ".",
    "view_changed": lambda d, c: f"the lens is covered as of {c}." if d["what"] == "covered" else f"the view changed at {c}.",
    "picture_poor": lambda d, c: f"the picture is too {d['what']} to see clearly.",
    "counting_stopped": lambda d, c: f"no visitors counted in the last {_plural(d['hours'], 'hour', 'hours')}.",
    "daily_summary": lambda d, c: str(d["headline"]),
    "weekly_summary": lambda d, c: str(d["headline"]),
}
def _tamil_for(minutes) -> str:
    m = int(float(minutes))
    return f"{m / 60:g} மணி நேரமா" if m >= 120 else f"{m} நிமிஷமா"


_TAMIL = {
    "crowding": lambda d, c: (f"{_tamil_for(d['minutes'])} " if d.get("minutes") else "இப்போ ") + f"{int(d['people'])} பேர் உள்ள இருக்காங்க.",
    "people_in_area": lambda d, c: f"இப்போ {int(d['people'])} பேர் இருக்காங்க.",
    "queue_long": lambda d, c: f"{int(d['length'])} பேர் காத்திருக்காங்க.",
    "phone_use": lambda d, c: f"{int(d['seconds'])} செகண்ட் போன் யூஸ் பண்றமாதிரி தெரியுது.",
    "blocked_plate": lambda d, c: f"{d['plate']} {c}-க்கு வந்துச்சு.",
    "vehicle_overstay": lambda d, c: f"{d['plate']} {_tamil_for(d['minutes'])} நிக்குது.",
    "low_confidence_plate": lambda d, c: "ஒரு நம்பர் பிளேட்ட கிளியரா படிக்க முடியல.",
    "after_hours": lambda d, c: f"கடை மூடி இருக்கும்போது {c}-க்கு மூவ்மெண்ட்.",
    "recorder_offline": lambda d, c: f"{c}-ல இருந்து சிக்னல் இல்ல.",
    "camera_offline": lambda d, c: f"{c}-ல இருந்து வீடியோ இல்ல.",
    "connection_unstable": lambda d, c: f"கடந்த 1 மணி நேரத்துல {int(d['drops'])} தடவ கனெக்ஷன் கட் ஆச்சு.",
    "back_to_normal": lambda d, c: f"{c}-க்கு திரும்ப ஆன்லைன் வந்துச்சு.",
    "recording_stopped": lambda d, c: f"{int(d['cameras'])} கேமரா வீடியோ ரெக்கார்ட் பண்ணல.",
    "disk_fault": lambda d, c: f"{d['disk']} வேல செய்யல." if d.get("disk") and d["disk"] != "No disk" else "வேல செய்யற டிஸ்க் எதுவும் இல்ல.",
    "disk_wear": lambda d, c: f"{d['disk']}-ல சுமார் {int(d['percent_left'])}% லைஃப் மீதி இருக்கு.",
    "storage_full": lambda d, c: "புது வீடியோக்கு இடம் இல்லன்னு ரெக்கார்டர் சொல்லுது.",
    "retention_low": lambda d, c: f"{int(d['days'])} நாள் வீடியோ தான் இருக்கு.",
    "clock_wrong": lambda d, c: f"கிளாக் {int(d['minutes_out'])} நிமிஷம் மாறி இருக்கு.",
    "recorder_restarted": lambda d, c: f"இன்னைக்கு {int(d['times'])} தடவ ரீஸ்டார்ட் ஆச்சு." if int(d["times"]) > 1 else "ஒரு தடவ ரீஸ்டார்ட் ஆச்சு.",
    "hardware_fault": lambda d, c: ", ".join(x for x in ["டெம்ப்பரேச்சர் வழக்கத்த விட அதிகம்" if d["hot"] else "", "ஃபேன் நின்னுருச்சு" if d["fan"] else "", "கேமரா பவர் போர்ட் வேல செய்யல" if d["poe"] else ""] if x) + ".",
    "view_changed": lambda d, c: f"{c}-ல இருந்து லென்ஸ் மூடி இருக்கு." if d["what"] == "covered" else f"{c}-க்கு வியூ மாறிருச்சு.",
    "picture_poor": lambda d, c: "பிக்சர் ரொம்ப இருட்டா இருக்கு." if d["what"] == "dark" else "பிக்சர் ரொம்ப ப்ளர் ஆ இருக்கு.",
    "counting_stopped": lambda d, c: f"கடந்த {int(d['hours'])} மணி நேரமா யாரும் கவுன்ட் ஆகல.",
    "daily_summary": lambda d, c: str(d["headline_ta"]),
    "weekly_summary": lambda d, c: str(d["headline_ta"]),
}
_NO_PLACE = {"daily_summary", "weekly_summary"}


def compose(rule_type: str, name: str, camera_name: str, details: dict, clock: str, title: str, body: str, tamil: bool | None = None) -> tuple[str, str]:
    """(title, text) for the phone. `name` is the alert's catalogue name; `title` and `body` are the alert's own, used when this one has no short form.
    The text is the English line, then (unless switched off) the same fact in Tamil on the next line."""
    if tamil is None:
        from ..config import get_settings
        tamil = get_settings().push_tamil
    make = _TEXT.get(rule_type)
    if make is None:
        return title, body
    try:
        fact = make(details, clock)
    except (KeyError, ValueError, TypeError):
        return name, body
    place = "" if rule_type in _NO_PLACE else ((details.get("area") if rule_type in ("people_in_area", "queue_long", "phone_use") else None) or details.get("what_recovered") or camera_name or details.get("recorder") or "")
    line = lambda f: f"{place}: {f}" if place else f[:1].upper() + f[1:]
    text = line(fact)
    if tamil:
        t = None
        try:
            fn = _TAMIL.get(rule_type)
            t = fn(details, clock) if fn else None
        except (KeyError, ValueError, TypeError):
            t = None
        if t:
            text += "\n" + line(t)
    return name, text
