"""Allow-list sanitiser for teacher-written rich text (the «word-processor» editor).
Only the tags and attributes below survive; everything else is removed (text is kept, escaped).
No inline styles, scripts, event handlers or links: formatting is done with <b>, <i>, <u>, <font face size color>.
Images and audio are references to uploaded files (data-file="<id>"), never to external addresses."""
import re
from html import escape
from html.parser import HTMLParser

ALLOWED = {"p", "br", "b", "strong", "i", "em", "u", "s", "ul", "ol", "li", "h2", "h3", "blockquote", "div", "span",
           "font", "img", "audio", "sub", "sup", "hr", "table", "thead", "tbody", "tr", "td", "th"}
VOID = {"br", "img", "hr"}
FONTS = {"Tahoma", "Arial", "Times New Roman", "Courier New", "Vazirmatn", "Segoe UI", "Georgia", "Verdana"}
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
MAX_LEN = 200_000


class _Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.files, self.skip = [], [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "iframe", "object", "embed"):
            self.skip += 1
            return
        if self.skip or tag not in ALLOWED:
            return
        a = {k: (v or "") for k, v in attrs}
        keep = []
        if tag == "font":
            if a.get("face") in FONTS:
                keep.append(f'face="{escape(a["face"], quote=True)}"')
            if a.get("size", "").isdigit() and 1 <= int(a["size"]) <= 7:
                keep.append(f'size="{a["size"]}"')
            if COLOR.match(a.get("color", "")):
                keep.append(f'color="{a["color"]}"')
        elif tag in ("img", "audio"):
            fid = a.get("data-file", "")
            if not fid.isdigit():
                return
            self.files.append(int(fid))
            keep.append(f'data-file="{int(fid)}"')
            if tag == "img":
                keep.append(f'alt="{escape(a.get("alt", "")[:150], quote=True)}"')
            else:
                keep.append("controls")
        elif tag in ("p", "div", "h2", "h3", "li", "blockquote") and a.get("dir") in ("rtl", "ltr"):
            keep.append(f'dir="{a["dir"]}"')
        elif tag == "span" and a.get("class") == "fx":
            keep.append('class="fx" dir="ltr"')                     # formula (typed expression, shown in a monospace box)
        elif tag in ("td", "th"):
            for k in ("colspan", "rowspan"):
                if a.get(k, "").isdigit() and int(a[k]) <= 10:
                    keep.append(f'{k}="{int(a[k])}"')
        self.out.append("<" + tag + ("".join(" " + k for k in keep)) + ">")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "iframe", "object", "embed"):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in ALLOWED and tag not in VOID and tag in self.stack:
            while self.stack:
                t = self.stack.pop()
                self.out.append(f"</{t}>")
                if t == tag:
                    break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(escape(data, quote=False))

    def close_all(self):
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")


def clean_html(html):
    """Return (safe_html, [file ids referenced])."""
    html = (html or "")[:MAX_LEN]
    c = _Cleaner()
    c.feed(html)
    c.close()
    c.close_all()
    return "".join(c.out), sorted(set(c.files))
