"""ASU term codes: "Spring 2027" <-> "2271".

ASU builds a term code as 2 + the last two digits of the year + a season digit
(Spring 1, Summer 4, Fall 7). /search/terms confirms this; the formula keeps us
from calling ASU just to translate a label.
"""

import re

SEASONS = {"spring": "1", "summer": "4", "fall": "7"}
SEASON_NAMES = {v: k.capitalize() for k, v in SEASONS.items()}


class TermError(ValueError):
    pass


def term_code(term):
    """Accept "Spring 2027", "spring 27", "2027 Spring" or "2271"; return "2271"."""
    text = str(term or "").strip().lower()
    if re.fullmatch(r"2\d{2}[147]", text):
        return text
    season = next((s for s in SEASONS if s in text), None)
    year = re.search(r"\b(20\d{2}|\d{2})\b", text)
    if not season or not year:
        raise TermError(f"unknown term: {term!r}")
    yy = year.group(1)[-2:]
    return f"2{yy}{SEASONS[season]}"


def term_label(code):
    code = term_code(code)
    return f"{SEASON_NAMES[code[3]]} 20{code[1:3]}"
