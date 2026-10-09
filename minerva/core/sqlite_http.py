import urllib.parse
import json
import re
from html.parser import HTMLParser
from minerva.constants import BASE_URL, log_error
from minerva.core.http import SITE_GATE, get_bytes

_ROM_JS_RE = re.compile(r"window\.rom\s*=\s*(\{.*?\});", re.DOTALL)


class EntryParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.entries = []
        self._in_entry = False
        self._skip = False
        self._href = None
        self._name = None
        self._size = None
        self._in_span = False
        self._entry_classes = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and "entry" in attrs.get("class", "").split():
            classes = attrs.get("class", "").split()
            if "search_back" in classes or "search_dir" in classes:
                self._skip = True
            else:
                self._skip = False
                self._in_entry = True
                self._href = None
                self._name = attrs.get("data-name", "")
                self._size = ""
        elif self._in_entry and not self._skip and tag == "a" and self._href is None:
            self._href = attrs.get("href", "")
        elif self._in_entry and not self._skip and tag == "span":
            self._in_span = True

    def handle_endtag(self, tag):
        if tag == "div" and self._in_entry:
            if (
                not self._skip
                and self._href
                and not self._href.lower().startswith("javascript:")
            ):
                is_folder = self._href.endswith("/")
                self.entries.append({
                    "name": self._name or urllib.parse.unquote(self._href.rstrip("/").split("/")[-1]),
                    "href": self._href,
                    "size": (self._size or "").strip(),
                    "is_folder": is_folder,
                })
            self._in_entry = False
            self._skip = False
            self._href = None
            self._name = None
            self._size = None
        elif tag == "span":
            self._in_span = False

    def handle_data(self, data):
        if self._in_entry and not self._skip and self._in_span:
            self._size = (self._size or "") + data


def fetch_entries(path: str) -> list[dict]:
    html = get_bytes(BASE_URL + path, timeout=15, retries=3, gate=SITE_GATE).decode("utf-8", errors="replace")
    parser = EntryParser()
    parser.feed(html)
    return parser.entries


def fetch_rom_info(rom_id: str) -> dict | None:
    url = f"{BASE_URL}/rom?id={rom_id}"
    html = get_bytes(url, timeout=45, retries=3, gate=SITE_GATE).decode("utf-8", errors="replace")
    match = _ROM_JS_RE.search(html)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except Exception as e:
        log_error(f"fetch_rom_info json parsing error for {rom_id}", e)
        return None


def extract_rom_id(href: str) -> str | None:
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    values = qs.get("id")
    if not values:
        return None
    return values[0]
