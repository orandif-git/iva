"""Frame-rate policy: which rate each camera is analysed at.

Every tenant has a policy (default: adaptive, 2 fps while the scene is empty, 5 fps while a person or vehicle is present). A camera
can override any part of it. The camera's own value wins; anything it leaves empty comes from the tenant."""

DEFAULT = {"mode": "adaptive", "idle": 2, "active": 5, "fixed": 5}
PARTS = ("mode", "idle", "active", "fixed")


def tenant_policy(row) -> dict:
    return {p: row[f"t_{p}"] for p in PARTS}


def camera_override(row) -> dict:
    return {p: row[f"c_{p}"] for p in PARTS}


def resolve(tenant: dict, override: dict) -> dict:
    """The rates a camera really runs at, and where each part came from."""
    eff, origin = {}, {}
    for p in PARTS:
        use_cam = override.get(p) is not None
        eff[p], origin[p] = (override[p] if use_cam else tenant[p]), ("camera" if use_cam else "tenant")
    idle = min(eff["idle"], eff["active"])                       # idle can never be faster than active
    return {
        "mode": eff["mode"], "idle_fps": idle, "active_fps": eff["active"], "fixed_fps": eff["fixed"],
        # The fastest the stream ever needs to be read. The worker thins it to the idle rate when the scene is empty.
        "capture_fps": eff["fixed"] if eff["mode"] == "fixed" else eff["active"],
        "from": origin,
    }
