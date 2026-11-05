"""Indian number plate cleaning and validation.

OCR confuses look-alike characters (O/0, I/1, S/5, B/8 ...). A plate has a fixed shape: two state letters, one or two district
digits, up to three series letters, four digits (for example GJ14AW1013). Because every position is known to be a letter or a
digit, a wrong-class character is usually fixable. We try every legal shape for the length, correct what can be corrected, and
choose the shape needing the fewest corrections. A read that needed more than one correction goes to a person instead of being
trusted. (Two shapes can never tie: they differ at one character, which is either a letter or a digit, so one shape always wins.)
"""
import re
from dataclasses import dataclass

STATES = set("AN AP AR AS BR CG CH DD DL DN GA GJ HP HR JH JK KA KL LA LD MH ML MN MP MZ NL OD OR PB PY RJ SK TN TR TS UK UP WB".split())
_TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "G": "6", "T": "7", "B": "8"}
_TO_LETTER = {"0": "O", "1": "I", "2": "Z", "5": "S", "6": "G", "8": "B"}
_BH = re.compile(r"^\d{2}BH\d{4}[A-Z]{1,2}$")                      # Bharat series, valid across states
REVIEW_CONFIDENCE = 70


@dataclass(frozen=True)
class PlateResult:
    text: str                  # best cleaned text (always set; may be invalid)
    valid: bool
    corrections: int = 0

    def needs_review(self, confidence: int | None) -> bool:
        return (not self.valid) or self.corrections > 1 or (confidence is not None and confidence < REVIEW_CONFIDENCE)


def normalize(raw: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (raw or "").upper())


_NEVER_FIXED_INTO_SERIES = {"I", "O"}      # series letters skip I and O (they look like 1 and 0), so a "fix" must not produce them


def _fit(chars: str, shape: str):
    """shape: L = state letter, S = series letter, D = digit."""
    out, fixes = [], 0
    for ch, want in zip(chars, shape):
        if want in "LS":
            if ch.isalpha():
                out.append(ch)
            elif ch in _TO_LETTER and not (want == "S" and _TO_LETTER[ch] in _NEVER_FIXED_INTO_SERIES):
                out.append(_TO_LETTER[ch]); fixes += 1
            else:
                return None
        else:
            if ch.isdigit():
                out.append(ch)
            elif ch in _TO_DIGIT:
                out.append(_TO_DIGIT[ch]); fixes += 1
            else:
                return None
    return "".join(out), fixes


def clean_plate(raw: str) -> PlateResult:
    s = normalize(raw)
    if _BH.match(s):
        return PlateResult(s, True)
    n = len(s)
    best: list = []
    for r in (1, 2):
        series = n - 6 - r
        if series < 0 or series > 3:
            continue
        fit = _fit(s, "LL" + "D" * r + "S" * series + "DDDD")
        if fit and fit[0][:2] in STATES:
            best.append(fit)
    if not best:
        return PlateResult(s, False)
    text, fixes = min(best, key=lambda f: f[1])
    return PlateResult(text, True, fixes)


def strict(raw: str) -> str | None:
    """A plate a human typed or confirmed: it must already be valid, with no guessing."""
    s = normalize(raw)
    r = clean_plate(s)
    return s if r.valid and r.corrections == 0 else None
