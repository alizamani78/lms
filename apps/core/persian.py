"""Persian text normalisation and the Iranian national-ID checksum."""
import re

_CHAR_MAP = str.maketrans({
    "ي": "ی", "ى": "ی", "ئ": "ئ", "ك": "ک", "ة": "ه", "ۀ": "ه", "ؤ": "ؤ",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4", "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
})
_INVISIBLE = re.compile("[\u200b\u200e\u200f\u202a-\u202e\ufeff\u00ad]")
_TEL_LINK = re.compile(r"\[([^\]]*)\]\(tel:[^)]*\)")        # "[970487177](tel:970487177)" copied from web pages
_MD_LINK = re.compile(r"\[([^\]]*)\]\((?:https?|mailto|tel):[^)]*\)")


def normalize_text(value):
    """Arabic ي/ك -> Persian ی/ک, Arabic/Persian digits -> ASCII, invisible marks removed, spaces collapsed.
    The zero-width non-joiner (U+200C) is kept because it is meaningful in Persian words."""
    if value is None:
        return ""
    s = str(value)
    s = _TEL_LINK.sub(r"\1", s)
    s = _MD_LINK.sub(r"\1", s)
    s = s.translate(_CHAR_MAP)
    s = _INVISIBLE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def canonical(value):
    """Comparison key: normalised, no spaces/ZWNJ, آ and ا unified, lower-case."""
    s = normalize_text(value).replace("\u200c", "").replace(" ", "").replace("آ", "ا")
    return s.lower()


def is_valid_national_id(code):
    """Standard Iranian national-ID (کد ملی) check: 10 digits, not all equal, checksum digit."""
    code = normalize_text(code)
    if not re.fullmatch(r"\d{10}", code) or len(set(code)) == 1:
        return False
    s = sum(int(code[i]) * (10 - i) for i in range(9)) % 11
    check = int(code[9])
    return check == s if s < 2 else check == 11 - s


def strip_links(text):
    """Remove '[text](tel:...)' / '[text](https://...)' wrappers copied from web pages, keeping the text."""
    text = _TEL_LINK.sub(r"\1", text)
    return _MD_LINK.sub(r"\1", text)
