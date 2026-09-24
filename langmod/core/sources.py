"""Where a mod's new versions come from, and fetching them.

Three kinds of place, all checked against the real services while this was
written (September 2026):

* **WT Live** (live.warthunder.com). Language mods are posted as another
  kind of content with a zip attached. ``POST /api/posts/get/`` with
  ``lang_group`` (the number in a post's address) gives one post and its
  file; ``POST /api/feed/get_user/`` with ``user`` gives an author's posts,
  newest first. Files come from ``/dl/<hash>/``, which sends you on to the
  CDN; no account is needed. Authors work in one of two ways: IFN1 keeps
  one post and swaps its file (the hash changes), the Localization
  Overhaul Project makes a new post each version. Following a post covers
  the first, following an author and a word in the file name the second.
  None of this is a published API: if WT Live changes, checks fail with a
  message and nothing else breaks.
* **GitHub** releases, through the public API: no account, 60 checks an
  hour.
* **Nexus Mods**, through its API with the player's own key. Only Premium
  accounts may download from a program; for everyone else the check says
  what is new and opens the download page. Nexus asks that a program other
  people use be registered with it rather than run on personal keys, so the
  standalone build, the copy that is handed round, leaves Nexus out
  (:data:`NEXUS`).
"""
from __future__ import annotations

import calendar
import html as html_lib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from http.client import HTTPException
from pathlib import Path
from typing import Any, Callable

from .. import __version__
from ..i18n import tr

USER_AGENT = f"LangmodManager/{__version__} (language mod update check)"
TIMEOUT = 12
MAX_DOWNLOAD = 300 * 1024 * 1024
LIVE = "https://live.warthunder.com"
NEXUS_API = "https://api.nexusmods.com/v1"
IMPORTABLE = (".zip", ".7z", ".csv")

#: Whether this copy may follow Nexus Mods pages. Nexus allows a personal API key only in a
#: player's own tools; a program handed to other people has to be registered with Nexus and
#: sign in through it instead. The standalone build is the copy that is handed round, so it
#: leaves Nexus out; running from source, it is the player's own tool. ``LANGMOD_NEXUS=0``
#: or ``1`` overrides.
NEXUS = os.environ.get("LANGMOD_NEXUS", "0" if getattr(sys, "frozen", False) else "1") != "0"


def places() -> str:
    """Where a mod can follow, for messages."""
    return tr("WT Live, GitHub or Nexus Mods") if NEXUS else tr("WT Live or GitHub")


class SourceError(Exception):
    """A source could not be read: offline, moved, or a key is missing."""


@dataclass
class Source:
    kind: str                  # "wtlive", "github" or "nexus"
    ref: str                   # wtlive: a post ("724816"'s lang_group) or "user:<id>"; github: "owner/repo";
                               # nexus: "game/mod id"
    match: str = ""            # a word the file's name or text must have, to pick one mod out of many
    label: str = ""            # how it reads in the window: "WT Live · InFerNos1"

    @property
    def page(self) -> str:
        if self.kind == "wtlive":
            return (f"{LIVE}/user/{self.ref[5:]}/" if self.ref.startswith("user:")
                    else f"{LIVE}/post/{self.ref}/en/")
        if self.kind == "github":
            return f"https://github.com/{self.ref}/releases"
        game, _, mod = self.ref.partition("/")
        return f"https://www.nexusmods.com/{game}/mods/{mod}?tab=files"

    @property
    def place(self) -> str:
        return {"wtlive": "WT Live", "github": "GitHub", "nexus": "Nexus Mods"}.get(self.kind, self.kind)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "Source | None":
        if not d or d.get("kind") not in ("wtlive", "github", "nexus") or not d.get("ref"):
            return None
        return cls(d["kind"], str(d["ref"]), d.get("match", ""), d.get("label", ""))


@dataclass
class Release:
    version: str
    file_name: str
    url: str                   # "" when the file cannot be fetched by a program (Nexus, not Premium)
    size: int
    published: float
    key: str                   # what identifies this exact file: it changes when the file does
    page: str
    title: str = ""
    note: str = ""             # why it cannot be downloaded, if it cannot

    @property
    def downloadable(self) -> bool:
        return bool(self.url)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "Release | None":
        if not d:
            return None
        try:
            return cls(**{k: d[k] for k in ("version", "file_name", "url", "size", "published", "key", "page")},
                       title=d.get("title", ""), note=d.get("note", ""))
        except (KeyError, TypeError):
            return None


# -- the network, swappable for tests ------------------------------------------------------------

class Http:
    @staticmethod
    def _unreachable(req: urllib.request.Request, exc: BaseException) -> SourceError:
        return SourceError(tr("could not reach {site}: {reason}", site=urllib.parse.urlsplit(req.full_url).netloc,
                              reason=getattr(exc, "reason", exc)))

    def _open(self, req: urllib.request.Request):
        try:
            return urllib.request.urlopen(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as exc:
            raise SourceError(tr("{site} said {code} {reason}", site=urllib.parse.urlsplit(req.full_url).netloc,
                                 code=exc.code, reason=exc.reason)) from exc
        except (OSError, HTTPException) as exc:
            raise self._unreachable(req, exc) from exc

    def _read(self, req: urllib.request.Request, r, size: int | None = None) -> bytes:
        """The answer, or as much of it as ``size``; the connection dropping or timing out halfway is a
        SourceError like any other failure to reach the site."""
        try:
            return r.read() if size is None else r.read(size)
        except (OSError, HTTPException) as exc:
            raise self._unreachable(req, exc) from exc

    def get_json(self, url: str, headers: dict | None = None) -> Any:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        with self._open(req) as r:
            return _json(self._read(req, r), url)

    def post_form(self, url: str, data: dict) -> Any:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                     headers={"User-Agent": USER_AGENT,
                                              "Content-Type": "application/x-www-form-urlencoded"})
        with self._open(req) as r:
            return _json(self._read(req, r), url)

    def download(self, url: str, dest: Path, expected: int = 0,
                 progress: Callable[[int, int], None] | None = None) -> Path:
        """Into ``dest`` by way of ``dest.part``, which goes again whatever stops the download. A
        connection that closes early just ends the reading, so the size is checked: the one the source
        gave, or else the one the server announced."""
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        tmp = dest.with_name(dest.name + ".part")
        got = 0
        try:
            with self._open(req) as r, open(tmp, "wb") as out:
                announced = r.headers.get("Content-Length", "")
                total = expected or (int(announced) if announced.isdigit() else 0)
                while chunk := self._read(req, r, 64 * 1024):
                    got += len(chunk)
                    if got > MAX_DOWNLOAD:
                        raise SourceError(tr("the download is far larger than any language mod; stopped"))
                    out.write(chunk)
                    if progress:
                        progress(got, total)
            if total and got != total:
                raise SourceError(tr("the download stopped short ({got} of {expected} bytes)", got=got,
                                     expected=total))
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, dest)
        return dest


def _json(raw: bytes, url: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SourceError(tr("{site} answered with something that is not JSON",
                             site=urllib.parse.urlsplit(url).netloc)) from exc


http = Http()


# -- versions -------------------------------------------------------------------------------------

_VERSION_PATTERNS = (
    re.compile(r"\bv(?:ersion)?\.?\s*(\d+(?:\.\d+)*)", re.I),           # V76, Version 76, v1.2
    re.compile(r"\brelease\s+(\d+(?:\.\d+)*)", re.I),                   # Release 1.3.09
    re.compile(r"(?<![\d.])(\d+\.\d+(?:\.\d+)*)(?![\d.])"),             # 1.3.09, 1.18.01
)


def version_of(*texts: str) -> str:
    for text in texts:
        text = re.sub(r"\.(zip|csv|7z|rar)$", "", (text or "").strip(), flags=re.I)   # "LOP_1.3.08.zip"
        for pattern in _VERSION_PATTERNS:
            m = pattern.search(text)
            if m:
                return m.group(1)
    return ""


def version_key(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def is_newer(candidate: str, current: str) -> bool | None:
    """True/False, or None when either side has no version to compare."""
    a, b = version_key(candidate), version_key(current)
    if not a or not b:
        return None
    return a > b


def _text(desc: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_lib.unescape(desc or ""))).strip()


_MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                        "september", "october", "november", "december"), 1)}
_DATE_DMY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+),?\s+(20\d\d)\b")
_DATE_MDY = re.compile(r"\b([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d\d)\b")
_DATE_ISO = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")


def date_in(text: str) -> float:
    """The first date written in a text ("17 September, 2026"), as a timestamp; 0 if none."""
    for pattern, order in ((_DATE_DMY, "dmy"), (_DATE_MDY, "mdy"), (_DATE_ISO, "ymd")):
        for m in pattern.finditer(text or ""):
            try:
                if order == "ymd":
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                else:
                    day, month = (m.group(1), m.group(2)) if order == "dmy" else (m.group(2), m.group(1))
                    mo = _MONTHS.get(month.lower())
                    if mo is None:
                        continue
                    y, d = int(m.group(3)), int(day)
                return time.mktime((y, mo, d, 12, 0, 0, 0, 0, -1))
            except (ValueError, OverflowError):
                continue
    return 0.0


# -- links ------------------------------------------------------------------------------------------

def parse_link(text: str) -> Source | None:
    """A mod's page, as the player would copy it, turned into something to follow."""
    t = (text or "").strip()
    m = re.search(r"live\.warthunder\.com/(?:\w\w/)?post/(\d+)", t)
    if m:
        return Source("wtlive", m.group(1))
    m = re.search(r"live\.warthunder\.com/(?:\w\w/)?user/(\d+)", t)
    if m:
        return Source("wtlive", f"user:{m.group(1)}")
    m = re.search(r"github\.com/([\w.-]+)/([\w.-]+)", t)
    if m:
        return Source("github", f"{m.group(1)}/{re.sub(r'\.git$', '', m.group(2))}")
    m = re.search(r"nexusmods\.com/(\w+)/mods/(\d+)", t)
    if m and NEXUS:
        return Source("nexus", f"{m.group(1)}/{m.group(2)}")
    return None


_NEXUS_NAME = re.compile(r"-(\d+)-\d+(?:-\d+)*-\d{9,}$")

#: Mods whose home is known, so following them needs no link at all.
KNOWN = (
    (re.compile(r"\bIFN1\b|^IFN1_", re.I), Source("wtlive", "694386", "IFN1", "WT Live · InFerNos1")),
    (re.compile(r"\bLOP\b|Localization Overhaul", re.I),
     Source("wtlive", "user:36978671", "LOP", "WT Live · Wiggly_Armed_Man")),
    (re.compile(r"WTHLM|Historical Locali[sz]ation", re.I),
     Source("github", "Addysaurus/lang_modding", "WTHLM", "GitHub · Addysaurus")),
)


def suggest(names: list[str]) -> Source | None:
    """Where a mod probably updates from, judging by its name, its files and its download's name."""
    for pattern, source in KNOWN:
        if any(pattern.search(n or "") for n in names):
            return Source(**source.to_dict())
    for n in names if NEXUS else ():
        m = _NEXUS_NAME.search(re.sub(r"\.(zip|7z|rar)$", "", Path(n or "").name, flags=re.I))
        if m:
            return Source("nexus", f"warthunder/{m.group(1)}", "", "Nexus Mods")
    return None


# -- asking each place what is newest --------------------------------------------------------------

def _live_release(post: dict) -> Release | None:
    f = post.get("file") or {}
    name, link = f.get("name") or "", f.get("link") or ""
    if not link or not name.lower().endswith(IMPORTABLE):
        return None
    if link.startswith("/"):
        link = LIVE + link
    text = _text(post.get("description", ""))
    key = link.rstrip("/").rsplit("/", 1)[-1]
    group = post.get("lang_group") or post.get("id")
    # A post whose file is swapped keeps the date it was first made (IFN1's is from 2018);
    # the date the author writes in it is the one that means something.
    published = max(float(post.get("created") or 0), date_in(text[:400]))
    return Release(version=version_of(name, text), file_name=name, url=link, size=int(f.get("size") or 0),
                   published=published, key=f"wtlive:{key}",
                   page=f"{LIVE}/post/{group}/en/", title=text[:160])


def _best(releases: list[Release], match: str) -> Release | None:
    if match:
        word = match.lower()
        releases = [r for r in releases if word in r.file_name.lower() or word in r.title.lower()]
    if not releases:
        return None
    return max(releases, key=lambda r: (version_key(r.version), r.published))


def _wtlive(source: Source) -> tuple[Release | None, str]:
    if source.ref.startswith("user:"):
        posts: list[dict] = []
        author = ""
        for page in range(3):
            data = http.post_form(f"{LIVE}/api/feed/get_user/", {"user": source.ref[5:], "page": str(page)})
            batch = ((data or {}).get("data") or {}).get("list") or []
            if not batch:
                break
            posts += batch
            author = author or (batch[0].get("author") or {}).get("nickname", "")
        best = _best([r for r in map(_live_release, posts) if r], source.match)
        return best, author
    data = http.post_form(f"{LIVE}/api/posts/get/", {"lang_group": source.ref, "language": "en"})
    post = (data or {}).get("data") if isinstance((data or {}).get("data"), dict) else data
    if not isinstance(post, dict) or not post.get("id"):
        raise SourceError(tr("WT Live has no such post (was it taken down?)"))
    return _live_release(post), (post.get("author") or {}).get("nickname", "")


def _github(source: Source) -> tuple[Release | None, str]:
    owner = source.ref.split("/", 1)[0]
    data = http.get_json(f"https://api.github.com/repos/{source.ref}/releases?per_page=10",
                         {"Accept": "application/vnd.github+json"})
    for rel in data if isinstance(data, list) else []:
        if rel.get("draft") or rel.get("prerelease"):
            continue
        tag = str(rel.get("tag_name") or "")
        published = _iso(rel.get("published_at"))
        assets = [a for a in rel.get("assets") or [] if str(a.get("name", "")).lower().endswith(IMPORTABLE)]
        if source.match:
            picked = [a for a in assets if source.match.lower() in a["name"].lower()] or assets
        else:
            picked = assets
        if picked:
            a = picked[0]
            return Release(version=version_of(tag, a["name"]) or tag.lstrip("vV"), file_name=a["name"],
                           url=a["browser_download_url"], size=int(a.get("size") or 0), published=published,
                           key=f"github:{a.get('id')}", page=rel.get("html_url", source.page),
                           title=str(rel.get("name") or tag)), owner
        if rel.get("zipball_url"):
            repo = source.ref.split("/", 1)[1]
            return Release(version=version_of(tag) or tag.lstrip("vV"), file_name=f"{repo}-{tag}.zip",
                           url=rel["zipball_url"], size=0, published=published, key=f"github:{rel.get('id')}:src",
                           page=rel.get("html_url", source.page), title=str(rel.get("name") or tag)), owner
    return None, owner


def _nexus(source: Source, api_key: str) -> tuple[Release | None, str]:
    if not api_key:
        raise SourceError(tr("Nexus Mods needs your API key first: Settings → Updates"))
    game, _, mod_id = source.ref.partition("/")
    headers = {"apikey": api_key, "Application-Name": "Langmod Manager", "Application-Version": __version__,
               "Accept": "application/json"}
    data = http.get_json(f"{NEXUS_API}/games/{game}/mods/{mod_id}/files.json?category=main", headers)
    files = [f for f in (data or {}).get("files") or []
             if str(f.get("file_name", "")).lower().endswith(IMPORTABLE)]
    if source.match:
        files = [f for f in files
                 if source.match.lower() in (str(f.get("name") or "") + str(f.get("file_name") or "")).lower()] or files
    if not files:
        return None, "Nexus Mods"
    f = max(files, key=lambda f: (f.get("uploaded_timestamp") or 0, version_key(str(f.get("version")))))
    rel = Release(version=str(f.get("version") or version_of(f.get("file_name", ""))), file_name=f["file_name"],
                  url="", size=int(f.get("size_in_bytes") or (f.get("size_kb") or 0) * 1024),
                  published=float(f.get("uploaded_timestamp") or 0), key=f"nexus:{f.get('file_id')}",
                  page=f"https://www.nexusmods.com/{game}/mods/{mod_id}?tab=files&file_id={f.get('file_id')}",
                  title=str(f.get("name") or ""))
    try:
        user = http.get_json(f"{NEXUS_API}/users/validate.json", headers)
        if (user or {}).get("is_premium"):
            links = http.get_json(f"{NEXUS_API}/games/{game}/mods/{mod_id}/files/{f.get('file_id')}/download_link.json",
                                  headers)
            if isinstance(links, list) and links and links[0].get("URI"):
                rel.url = links[0]["URI"]
    except SourceError:
        pass
    if not rel.url:
        rel.note = tr("Nexus Mods only lets Premium members download from a program. Download it from the page, "
                      "then add it to this mod with Update.")
    return rel, "Nexus Mods"


def _iso(text: str | None) -> float:
    if not text:
        return 0.0
    try:
        return float(calendar.timegm(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        return 0.0


def latest(source: Source, nexus_key: str = "") -> Release | None:
    """The newest release a source has for this mod; fills in the source's label as a side effect."""
    if source.kind == "wtlive":
        rel, who = _wtlive(source)
    elif source.kind == "github":
        rel, who = _github(source)
    elif source.kind == "nexus":
        if not NEXUS:
            raise SourceError(tr("this copy of the manager does not check Nexus Mods: follow the mod's WT Live or "
                                 "GitHub page instead"))
        rel, who = _nexus(source, nexus_key)
    else:
        raise SourceError(f"unknown kind of source {source.kind!r}")
    if not source.label and who:
        source.label = f"{source.place} · {who}"
    return rel


def download(release: Release, folder: Path, progress=None) -> Path:
    if not release.url:
        raise SourceError(release.note or "this file cannot be downloaded by a program")
    folder.mkdir(parents=True, exist_ok=True)
    # A name Windows can store: no path characters, no dot or space at the end, not a device (CON.zip).
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", release.file_name).rstrip(". ") or "download.zip"
    if re.match(r"(con|prn|aux|nul|com\d|lpt\d)(\.|$)", name, re.I):
        name = "_" + name
    return http.download(release.url, folder / name, release.size, progress)

