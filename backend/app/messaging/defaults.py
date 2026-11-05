"""The WhatsApp templates Jas iVision uses, ready to submit for approval.

Meta's rules these follow: UTILITY category (a transactional alert the customer asked for); numbered variables {{1}}, {{2}}...;
the body does not begin or end with a variable; each variable has a sample; no promotional wording; the header media type is fixed at approval.
`variables` lists, in order, which alert detail fills {{1}}, {{2}}, ..."""

DEFAULT_TEMPLATES = [
    {"channel": "whatsapp", "key": "alert_image", "provider_template": "gi_alert_image", "language": "en", "category": "utility",
     "description": "Alert with the evidence picture on top and a button that opens the full evidence page.",
     "body_text": "Security alert from Jas iVision.\n\nEvent: {{1}}\nLocation: {{2}}\nTime: {{3}}\nDetails: {{4}}\n\nTap the button below to see the full evidence.",
     "media_type": "image", "link_button": True, "variables": ["event", "location", "time", "details"],
     "samples": ["Blocked vehicle TN72AB1234", "Main Gate", "05 Nov, 10:30 AM", "Seen entering"], "button_text": "View evidence",
     "button_url": "https://app.example.com/e/{{1}}"},
    {"channel": "whatsapp", "key": "alert_link", "provider_template": "gi_alert_link", "language": "en", "category": "utility",
     "description": "Same alert without a picture on top. Used while the picture template is not approved yet.",
     "body_text": "Security alert from Jas iVision.\n\nEvent: {{1}}\nLocation: {{2}}\nTime: {{3}}\nDetails: {{4}}\n\nTap the button below to see the full evidence.",
     "media_type": "none", "link_button": True, "variables": ["event", "location", "time", "details"],
     "samples": ["Blocked vehicle TN72AB1234", "Main Gate", "05 Nov, 10:30 AM", "Seen entering"], "button_text": "View evidence",
     "button_url": "https://app.example.com/e/{{1}}"},
    {"channel": "whatsapp", "key": "alert_text", "provider_template": "gi_alert_text", "language": "en", "category": "utility",
     "description": "Plain alert with no evidence, for example a camera or recorder that went offline.",
     "body_text": "Jas iVision notice.\n\nEvent: {{1}}\nLocation: {{2}}\nTime: {{3}}\nDetails: {{4}}\n\nPlease check on site or contact support.",
     "media_type": "none", "link_button": False, "variables": ["event", "location", "time", "details"],
     "samples": ["Camera offline", "Main Gate", "05 Nov, 10:30 AM", "No video for 5 minutes"]},
    {"channel": "whatsapp", "key": "alert_clip", "provider_template": "gi_alert_clip", "language": "en", "category": "utility",
     "description": "Alert with a short video clip on top. Phase 2: not used until clips are switched on.",
     "body_text": "Security alert from Jas iVision.\n\nEvent: {{1}}\nLocation: {{2}}\nTime: {{3}}\nDetails: {{4}}\n\nTap the button below to see the full evidence.",
     "media_type": "video", "link_button": True, "variables": ["event", "location", "time", "details"],
     "samples": ["Blocked vehicle TN72AB1234", "Main Gate", "05 Nov, 10:30 AM", "Seen entering"], "button_text": "View evidence",
     "button_url": "https://app.example.com/e/{{1}}"},
]

# What each alert type is called to people, and how serious it is.
ALERT_TYPES = {
    "blocked_plate": {"title": "Blocked vehicle seen", "severity": "critical", "uses_evidence": True,
                      "description": "A vehicle on your blocked list is read at any camera.",
                      "params": {"clip": {"type": "boolean", "default": True}}},
    "vehicle_overstay": {"title": "Vehicle has stayed too long", "severity": "warning", "uses_evidence": False,
                         "description": "A vehicle entered and has not left after the time you set.", "params": {"max_hours": {"type": "number", "default": 8, "min": 1, "max": 168}}},
    "crowding": {"title": "Too many people", "severity": "warning", "uses_evidence": False,
                 "description": "The number of people counted inside goes above your limit.", "params": {"people": {"type": "integer", "default": 50, "min": 1, "max": 100000}}},
    "camera_offline": {"title": "Camera offline", "severity": "warning", "uses_evidence": False,
                       "description": "A camera stops sending video.", "params": {}},
    "recorder_offline": {"title": "Recorder offline", "severity": "warning", "uses_evidence": False,
                         "description": "Your DVR/NVR stops contacting us.", "params": {}},
    "low_confidence_plate": {"title": "Plate needs checking", "severity": "info", "uses_evidence": True,
                             "description": "A number plate could not be read with confidence and needs a person to check it.", "params": {}},
}
