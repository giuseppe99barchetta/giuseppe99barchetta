#!/usr/bin/env python3
"""Generate the macOS-Terminal-style SVGs used by the profile README.

Pure standard library. Data comes from the GitHub REST API (profile, repos,
languages), the GraphQL API when a token is available (commits, PRs, issues,
contribution calendar) and Docker Hub (pull count). Anything that cannot be
fetched is left out of the cards instead of being invented.

Usage:
    GH_TOKEN=... python scripts/profile_gen.py              # fetch + render
    python scripts/profile_gen.py --data data.json          # render from a dump
    python scripts/profile_gen.py --dump data.json          # fetch + save dump
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import math
import os
import pathlib
import urllib.error
import urllib.request
from html import escape

# --------------------------------------------------------------------------
# Config: edit freely
# --------------------------------------------------------------------------
USER = "giuseppe99barchetta"
HOST = "homelab"
SHORT = "giuseppe"
ROLE = "Full-Stack Developer · Open Source"
LOCATION = "Italy"
OS_LINE = "Linux · Proxmox VE · Docker"
FLAGSHIP = "SuggestArr"
DOCKER_IMAGE = "ciuse99/suggestarr"
LANG_EXCLUDE = {"HTML", "CSS", "SCSS", "Less", "Dockerfile", "Makefile",
                "Procfile", "Batchfile", "Jinja", "Smarty"}
PROJECT_EXCLUDE = {USER, "FordConnect-HA"}  # repos never shown in the ls card
PROJECTS_SHOWN = 6

# Clickable buttons under the projects card (repo names on GitHub).
LINKED_REPOS = ["SuggestArr", "Google-Photos-Uploader", "home_maintenance", "MediaFlowrr"]
SOCIAL = [  # (file stem, label, detail)
    ("linkedin", "linkedin", "in/giuseppe-barchetta"),
    ("sponsor", "sponsor", "github.com/sponsors"),
]

# The homelab is described, not measured: no fake uptimes or ports.
SERVICES = [
    ("suggestarr", DOCKER_IMAGE),
    ("jellyfin", "jellyfin/jellyfin"),
    ("seerr", "ghcr.io/seerr-team/seerr"),
    ("radarr", "lscr.io/linuxserver/radarr"),
    ("sonarr", "lscr.io/linuxserver/sonarr"),
    ("immich-server", "ghcr.io/immich-app/immich-server"),
    ("audiobookshelf", "ghcr.io/advplyr/audiobookshelf"),
    ("calibre-web", "lscr.io/linuxserver/calibre-web"),
    ("home-assistant", "ghcr.io/home-assistant/home-assistant"),
    ("vaultwarden", "vaultwarden/server"),
    ("adguardhome", "adguard/adguardhome"),
    ("zoraxy", "zoraxydocker/zoraxy"),
    ("uptime-kuma", "louislam/uptime-kuma"),
    ("komodo", "ghcr.io/moghtech/komodo-core"),
]
HOMELAB_FOOTER = "# + Proxmox VE & Backup Server · GL.iNet Flint 3 · WireGuard · and many more"

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT.parent / "profile"

# --------------------------------------------------------------------------
# Palette / metrics: macOS Terminal, dark appearance
# --------------------------------------------------------------------------
WIN = "#1e1e1e"
BAR_TOP = "#3b3b3d"
BAR_BOT = "#303032"
EDGE = "#4b4b4f"
TXT = "#ebebf0"
DIM = "#9d9da4"
FAINT = "#4a4a50"
CYAN = "#5ac8fa"
GREEN_OK = "#32d74b"
STAR = "#ffd60a"
# retro six-stripe rainbow, brightened for dark backgrounds
RAINBOW = ["#6cc24a", "#ffc72c", "#ff8f1f", "#ff4b4b", "#c362d6", "#2fb3ff"]
LANG_COLORS = {"Python": "#2fb3ff", "Vue": "#6cc24a", "JavaScript": "#ffc72c",
               "Java": "#ff8f1f", "TypeScript": "#5ac8fa", "CSS": "#c362d6",
               "Shell": "#6cc24a", "Go": "#5ac8fa", "Rust": "#ff8f1f"}
LIGHTS = [("#ff5f57", "#e0443e"), ("#febc2e", "#d89e24"), ("#28c840", "#1aab29")]

FS = 14                 # font size (px)
CW = FS * 0.6           # JetBrains Mono advance = 600/1000 em
LH = 22                 # line height
PAD = 24                # inner padding
BAR = 30                # title bar height
M = 12                  # outer margin (room for the window shadow)
W = 860                 # total SVG width
WW = W - 2 * M          # window width


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
def _req(url: str, token: str | None, body: dict | None = None):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"{USER}-profile-gen"}
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


REPO_KEYS = ("name", "description", "language", "stargazers_count", "forks_count",
             "fork", "archived", "html_url")


def fetch(token: str | None) -> dict:
    api = "https://api.github.com"
    user = _req(f"{api}/users/{USER}", token)
    repos, page = [], 1
    while True:
        batch = _req(f"{api}/users/{USER}/repos?per_page=100&type=owner&page={page}", token)
        repos += batch
        if len(batch) < 100:
            break
        page += 1

    langs: dict[str, int] = {}
    for r in repos:
        if r["fork"] or r["archived"]:
            continue
        try:
            for k, v in _req(r["languages_url"], token).items():
                langs[k] = langs.get(k, 0) + v
        except urllib.error.HTTPError:
            pass

    data = {
        "created_at": user["created_at"],
        "followers": user["followers"],
        "public_repos": user["public_repos"],
        "repos": [{k: r[k] for k in REPO_KEYS} for r in repos],
        "languages": langs,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        hub = _req(f"https://hub.docker.com/v2/repositories/{DOCKER_IMAGE}/", None)
        data["docker_pulls"] = hub["pull_count"]
    except Exception as e:
        print(f"warning: docker pulls unavailable ({e})")
    if token:
        try:
            data["contrib"] = fetch_contrib(token, user["created_at"])
        except Exception as e:  # GraphQL is a bonus, never a hard failure
            print(f"warning: contributions unavailable ({e})")
    return data


def fetch_contrib(token: str, created_at: str) -> dict:
    q = """query($u:String!,$f:DateTime!,$t:DateTime!){user(login:$u){
      contributionsCollection(from:$f,to:$t){
        totalCommitContributions restrictedContributionsCount
        totalPullRequestContributions totalIssueContributions
        contributionCalendar{weeks{contributionDays{date contributionCount}}}}}}"""
    start = int(created_at[:4])
    now = dt.datetime.now(dt.timezone.utc)
    commits = prs = issues = private = 0
    days: dict[str, int] = {}
    for year in range(start, now.year + 1):
        f = f"{year}-01-01T00:00:00Z"
        t = now.isoformat() if year == now.year else f"{year}-12-31T23:59:59Z"
        res = _req("https://api.github.com/graphql", token,
                   {"query": q, "variables": {"u": USER, "f": f, "t": t}})
        if "errors" in res:
            raise RuntimeError(res["errors"][0].get("message"))
        c = res["data"]["user"]["contributionsCollection"]
        commits += c["totalCommitContributions"]
        private += c["restrictedContributionsCount"]
        prs += c["totalPullRequestContributions"]
        issues += c["totalIssueContributions"]
        for w in c["contributionCalendar"]["weeks"]:
            for d in w["contributionDays"]:
                days[d["date"]] = d["contributionCount"]
    return {"commits": commits, "private": private, "prs": prs,
            "issues": issues, "days": days}


# --------------------------------------------------------------------------
# Derived numbers
# --------------------------------------------------------------------------
def fmt_k(n: int) -> str:
    for div, suf in ((1_000_000, "M"), (1000, "k")):
        if n >= div:
            return f"{n / div:.1f}".rstrip("0").rstrip(".") + suf
    return str(n)


def uptime(created_at: str, now: dt.date) -> str:
    c = dt.date.fromisoformat(created_at[:10])
    months = (now.year - c.year) * 12 + now.month - c.month - (now.day < c.day)
    y, m = divmod(months, 12)
    parts = [f"{y} year{'s' * (y != 1)}"] if y else []
    parts.append(f"{m} month{'s' * (m != 1)}")
    return ", ".join(parts)


def streaks(days: dict[str, int], today: dt.date) -> tuple[int, int]:
    best = run = 0
    for k in sorted(days):
        run = run + 1 if days[k] > 0 else 0
        best = max(best, run)
    cur, d = 0, today
    if days.get(d.isoformat(), 0) == 0:  # today not started yet: don't break the streak
        d -= dt.timedelta(days=1)
    while days.get(d.isoformat(), 0) > 0:
        cur += 1
        d -= dt.timedelta(days=1)
    return cur, best


def weekly(days: dict[str, int], today: dt.date, weeks: int = 52) -> list[int]:
    out = []
    start = today - dt.timedelta(days=today.weekday() + 7 * (weeks - 1))
    for w in range(weeks):
        s = start + dt.timedelta(days=7 * w)
        out.append(sum(days.get((s + dt.timedelta(days=i)).isoformat(), 0) for i in range(7)))
    return out


def top_langs(langs: dict[str, int], n: int = 6) -> list[tuple[str, float]]:
    items = [(k, v) for k, v in langs.items() if k not in LANG_EXCLUDE]
    total = sum(v for _, v in items) or 1
    items.sort(key=lambda kv: -kv[1])
    return [(k, v * 100 / total) for k, v in items[:n]]


def own_repos(data: dict) -> list[dict]:
    return [r for r in data["repos"]
            if not r["fork"] and not r["archived"] and r["name"] not in PROJECT_EXCLUDE]


# --------------------------------------------------------------------------
# SVG building blocks
# --------------------------------------------------------------------------
FONT_SLOT = "/*FONT*/"


def _font_face() -> str:
    return FONT_SLOT


def _embed_fonts(svg: str) -> str:
    """Embed JetBrains Mono, subset to the characters this SVG actually uses
    when fontTools is available (keeps each file small), else the full subset."""
    import re
    from html import unescape
    chars = set(unescape(re.sub(r"<[^>]+>", "", svg.split("</defs>", 1)[-1]))) | set("0123456789")
    out = []
    for weight, name in ((400, "Regular"), (700, "Bold")):
        raw = (ROOT / "fonts" / f"JetBrainsMonoNL-{name}.subset.woff2").read_bytes()
        try:
            import io
            from fontTools import subset
            from fontTools.ttLib import TTFont
            font = TTFont(io.BytesIO(raw))
            sub = subset.Subsetter(subset.Options(flavor="woff2", layout_features=[]))
            sub.populate(text="".join(chars))
            sub.subset(font)
            font.flavor = "woff2"
            buf = io.BytesIO()
            font.save(buf)
            raw = buf.getvalue()
        except Exception:
            pass
        out.append(f"@font-face{{font-family:'JBM';font-weight:{weight};"
                   f"src:url(data:font/woff2;base64,{base64.b64encode(raw).decode()}) format('woff2')}}")
    return svg.replace(FONT_SLOT, "".join(out), 1)


CSS = f"""
text{{font-family:'JBM','SF Mono',Menlo,Consolas,monospace;font-size:{FS}px;fill:{TXT};white-space:pre}}
.d{{fill:{DIM}}} .f{{fill:{FAINT}}} .c{{fill:{CYAN}}} .w{{font-weight:700}}
.r{{opacity:0;animation:on .01s linear forwards}}
@keyframes on{{to{{opacity:1}}}}
.cur{{animation:blink 1.05s steps(1) infinite}}
@keyframes blink{{50%{{opacity:0}}}}
.slide{{opacity:0;transform:translateX(-14px);animation:slide .45s cubic-bezier(.2,.8,.2,1) forwards}}
@keyframes slide{{to{{opacity:1;transform:none}}}}
.grow{{transform-box:fill-box;transform-origin:50% 100%;transform:scaleY(0);animation:grow .5s cubic-bezier(.2,.8,.2,1) forwards}}
@keyframes grow{{to{{transform:scaleY(1)}}}}
.pulse{{animation:pulse 2.2s ease-in-out infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.35}}}}
@media (prefers-reduced-motion:reduce){{.r,.slide,.grow,.pulse,.cur{{animation:none;opacity:1;transform:none}}}}
"""


def t(x: float, y: float, s: str, cls: str = "", delay: float | None = None, fill: str | None = None) -> str:
    c = cls + (" r" if delay is not None else "")
    st = f' style="animation-delay:{delay:.2f}s"' if delay is not None else ""
    fl = f' fill="{fill}" style="fill:{fill}{";animation-delay:%.2fs" % delay if delay is not None else ""}"' if fill else ""
    if fill:
        st = ""
    return f'<text x="{x:.1f}" y="{y:.1f}" class="{c.strip()}"{st}{fl}>{escape(s)}</text>'


def prompt_spans() -> str:
    return (f'<tspan class="w">{SHORT}@{HOST}</tspan><tspan> </tspan>'
            f'<tspan class="c w">~</tspan><tspan class="d"> % </tspan>')


PROMPT_LEN = len(f"{SHORT}@{HOST} ~ % ")


def typed(x: float, y: float, cmd: str, begin: float, cps: float = 22, uid: str = "c") -> tuple[str, float]:
    """Prompt + command revealed char by char (SMIL, works inside <img>)."""
    n = len(cmd)
    dur = max(n / cps, 0.2)
    cx = x + PROMPT_LEN * CW
    widths = ";".join(f"{i * CW:.1f}" for i in range(n + 1))
    xs = ";".join(f"{cx + i * CW:.1f}" for i in range(n + 1))
    end = begin + dur
    svg = (
        f'<clipPath id="{uid}"><rect x="{cx:.1f}" y="{y - FS:.1f}" height="{LH}" width="0">'
        f'<animate attributeName="width" values="{widths}" calcMode="discrete" begin="{begin:.2f}s" dur="{dur:.2f}s" fill="freeze"/>'
        f'</rect></clipPath>'
        f'<text x="{x:.1f}" y="{y:.1f}">{prompt_spans()}</text>'
        f'<text x="{cx:.1f}" y="{y:.1f}" clip-path="url(#{uid})">{escape(cmd)}</text>'
        f'<g opacity="1"><set attributeName="opacity" to="0" begin="{end + .15:.2f}s" fill="freeze"/>'
        f'<rect class="cur" x="{cx:.1f}" y="{y - FS + 1:.1f}" width="{CW:.1f}" height="{FS + 4}" fill="{TXT}" opacity=".85">'
        f'<animate attributeName="x" values="{xs}" calcMode="discrete" begin="{begin:.2f}s" dur="{dur:.2f}s" fill="freeze"/>'
        f'</rect></g>'
    )
    return svg, end + 0.25


def idle_prompt(x: float, y: float, delay: float) -> str:
    cx = x + PROMPT_LEN * CW
    return (f'<g class="r" style="animation-delay:{delay:.2f}s">'
            f'<text x="{x:.1f}" y="{y:.1f}">{prompt_spans()}</text>'
            f'<rect class="cur" x="{cx:.1f}" y="{y - FS + 1:.1f}" width="{CW:.1f}" height="{FS + 4}" fill="{TXT}" opacity=".85"/></g>')


def frame(height: int, title: str, body: str, label: str) -> str:
    """macOS Terminal window: traffic lights, title bar, soft shadow."""
    H = height + 2 * M
    lights = "".join(
        f'<circle cx="{18 + i * 20}" cy="{BAR / 2}" r="6" fill="{f}" stroke="{s}" stroke-width=".8"/>'
        for i, (f, s) in enumerate(LIGHTS))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="{escape(label)}">
<title>{escape(label)}</title>
<defs>
<style>{_font_face()}{CSS}</style>
<linearGradient id="bar" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{BAR_TOP}"/><stop offset="1" stop-color="{BAR_BOT}"/></linearGradient>
<linearGradient id="rb" x1="0" y1="0" x2="1" y2="0">{"".join(f'<stop offset="{i / 5:.2f}" stop-color="{c}"/>' for i, c in enumerate(RAINBOW))}</linearGradient>
<filter id="sh" x="-5%" y="-5%" width="110%" height="115%"><feDropShadow dx="0" dy="4" stdDeviation="5" flood-color="#000" flood-opacity=".35"/></filter>
<clipPath id="win"><rect width="{WW}" height="{height}" rx="10"/></clipPath>
</defs>
<rect x="{M}" y="{M}" width="{WW}" height="{height}" rx="10" fill="{WIN}" filter="url(#sh)"/>
<g transform="translate({M} {M})">
<g clip-path="url(#win)">
<rect width="{WW}" height="{height}" fill="{WIN}"/>
<rect width="{WW}" height="{BAR}" fill="url(#bar)"/>
<line x1="0" y1="{BAR - .5}" x2="{WW}" y2="{BAR - .5}" stroke="#111"/>
{lights}
<text x="{WW / 2}" y="{BAR / 2 + 4.5}" text-anchor="middle" class="d w" style="font-size:12.5px">{escape(title)}</text>
{body}
</g>
<rect x=".5" y=".5" width="{WW - 1}" height="{height - 1}" rx="10" fill="none" stroke="{EDGE}"/>
</g>
</svg>
'''


def title_for(cmd: str, rows: int = 0) -> str:
    return f"{SHORT} — {cmd}"


def star_icon(x: float, y: float, r: float = 5.4, fill: str = STAR) -> str:
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append(f"{x + rr * math.cos(a):.1f},{y + rr * math.sin(a):.1f}")
    return f'<polygon points="{" ".join(pts)}" fill="{fill}"/>'


def fork_icon(x: float, y: float) -> str:
    return (f'<g fill="none" stroke="{DIM}" stroke-width="1.3">'
            f'<circle cx="{x - 3.2:.1f}" cy="{y - 4:.1f}" r="1.5"/><circle cx="{x + 3.2:.1f}" cy="{y - 4:.1f}" r="1.5"/>'
            f'<circle cx="{x:.1f}" cy="{y + 4.5:.1f}" r="1.5"/>'
            f'<path d="M{x - 3.2:.1f} {y - 2.5:.1f}v1.5q0 2 3.2 2t3.2-2v-1.5M{x:.1f} {y + 1:.1f}v2"/></g>')


# --------------------------------------------------------------------------
# Monogram: pixel "GB" with six rainbow stripes
# --------------------------------------------------------------------------
GLYPHS = {
    "G": ["..####..", ".##..##.", "##....##", "##......", "##......", "##......",
          "##..####", "##....##", "##....##", "##....##", ".##..##.", "..####.."],
    "B": ["######..", "##...##.", "##....##", "##....##", "##...##.", "######..",
          "##...##.", "##....##", "##....##", "##....##", "##...##.", "######.."],
}


def monogram(x: float, y: float, px: float, begin: float) -> tuple[str, float, float]:
    rows = [GLYPHS["G"][i] + ".." + GLYPHS["B"][i] for i in range(12)]
    out = []
    for stripe in range(6):
        rects = []
        for r in (stripe * 2, stripe * 2 + 1):
            row, c = rows[r], 0
            while c < len(row):  # merge horizontal runs into one rect
                if row[c] == "#":
                    s = c
                    while c < len(row) and row[c] == "#":
                        c += 1
                    rects.append(f'<rect x="{x + s * px:.1f}" y="{y + r * px:.1f}" '
                                 f'width="{(c - s) * px:.1f}" height="{px:.1f}"/>')
                else:
                    c += 1
        out.append(f'<g fill="{RAINBOW[stripe]}" class="slide" style="animation-delay:{begin + stripe * .07:.2f}s">'
                   f'{"".join(rects)}</g>')
    return "".join(out), len(rows[0]) * px, 12 * px


# --------------------------------------------------------------------------
# Cards
# --------------------------------------------------------------------------
def card_neofetch(data: dict, today: dt.date) -> str:
    x0 = PAD
    y = BAR + PAD + 6
    body = []
    s, t0 = typed(x0, y, "neofetch", 0.35, uid="nf")
    body.append(s)
    y += LH + 14

    repos = own_repos(data)
    stars = sum(r["stargazers_count"] for r in data["repos"] if not r["fork"])
    flag = next((r for r in repos if r["name"].lower() == FLAGSHIP.lower()), None)
    langs = top_langs(data.get("languages", {}), 4)
    contrib = data.get("contrib")

    info: list[tuple[str, str]] = [
        ("OS", OS_LINE),
        ("Host", f"{HOST} · {LOCATION}"),
        ("Role", ROLE),
        ("Uptime", f"{uptime(data['created_at'], today)} on GitHub"),
        ("Repos", f"{data['public_repos']} public · {stars:,} stars earned"),
        ("Followers", f"{data['followers']:,}"),
    ]
    if langs:
        info.append(("Languages", " · ".join(k for k, _ in langs)))
    if contrib:
        info.append(("Commits", f"{contrib['commits'] + contrib['private']:,}   "
                                f"PRs {contrib['prs']:,}   Issues {contrib['issues']:,}"))
        cur, best = streaks(contrib["days"], today)
        info.append(("Streak", f"{cur} day{'s' * (cur != 1)} · best {best}"))
    if flag:
        extra = f" · {fmt_k(data['docker_pulls'])} pulls" if data.get("docker_pulls") else ""
        info.append(("Flagship", f"{flag['name']} · {fmt_k(flag['stargazers_count'])} stars{extra}"))

    info_h = (len(info) + 4) * LH
    px = 15.0
    mono_w, mono_h = 18 * px, 12 * px
    my = y - FS + (info_h - mono_h) / 2 - LH / 2
    mono, _, _ = monogram(x0 + 6, my, px, t0)
    body.append(mono)

    ix = x0 + mono_w + 50
    kx = 11
    d = t0 + 0.2
    body.append(f'<text x="{ix:.1f}" y="{y:.1f}" class="r" style="animation-delay:{d:.2f}s">'
                f'<tspan class="w" fill="{RAINBOW[5]}" style="fill:{RAINBOW[5]}">{SHORT}</tspan><tspan class="d">@</tspan>'
                f'<tspan class="w" fill="{RAINBOW[5]}" style="fill:{RAINBOW[5]}">{HOST}</tspan></text>')
    body.append(t(ix, y + LH, "-" * len(f"{SHORT}@{HOST}"), "d", d))
    for i, (k, v) in enumerate(info):
        yy = y + (i + 2) * LH
        dd = d + 0.07 * (i + 1)
        col = RAINBOW[i % 6]
        body.append(f'<g class="r" style="animation-delay:{dd:.2f}s">'
                    f'<text x="{ix:.1f}" y="{yy:.1f}" class="w" style="fill:{col}">{escape(k)}</text>'
                    f'<text x="{ix + kx * CW:.1f}" y="{yy:.1f}">{escape(v)}</text></g>')

    # neofetch colour blocks (macOS Terminal ANSI palette), two rows
    yy = y + (len(info) + 2) * LH + 6
    dd = d + 0.07 * (len(info) + 2)
    normal = ["#000000", "#c23621", "#25bc24", "#adad27", "#492ee1", "#d338d3", "#33bbc8", "#cbcccd"]
    bright = ["#818383", "#fc391f", "#31e722", "#eaec23", "#5833ff", "#f935f8", "#14f0f0", "#e9ebeb"]
    bw = 3 * CW
    blocks = "".join(f'<rect x="{ix + i * bw:.1f}" y="{yy:.1f}" width="{bw:.1f}" height="14" fill="{c}"/>'
                     for i, c in enumerate(normal))
    blocks += "".join(f'<rect x="{ix + i * bw:.1f}" y="{yy + 14:.1f}" width="{bw:.1f}" height="14" fill="{c}"/>'
                      for i, c in enumerate(bright))
    body.append(f'<g class="r" style="animation-delay:{dd:.2f}s">{blocks}</g>')

    y_end = y + info_h + 12
    body.append(idle_prompt(x0, y_end, dd + 0.3))
    h = int(y_end + PAD - 4)
    return frame(h, title_for("neofetch", 30), "".join(body), f"{SHORT}@{HOST} — neofetch")


def card_projects(data: dict) -> str:
    x0 = PAD
    y = BAR + PAD + 6
    repos = sorted(own_repos(data), key=lambda r: (-r["stargazers_count"], r["name"].lower()))[:PROJECTS_SHOWN]
    body = []
    s, t0 = typed(x0, y, f"ls -l ~/projects --sort=stars | head -{len(repos)}", 0.3, uid="pj")
    body.append(s)
    y += LH + 10
    cols = {"name": 0, "stars": 27, "forks": 35, "lang": 42, "desc": 55}
    body.append(t(x0, y, f"total {len(own_repos(data))}", "d", t0))
    y += LH
    max_desc = int((WW - 2 * PAD) / CW) - cols["desc"]
    for i, r in enumerate(repos):
        yy = y + i * LH
        d = t0 + 0.1 + i * 0.08
        desc = (r["description"] or "").strip().replace("\n", " ")
        if len(desc) > max_desc:
            desc = desc[: max_desc - 1].rstrip() + "…"
        name = r["name"] if len(r["name"]) <= 25 else r["name"][:24] + "…"
        lang = r["language"] or "-"
        g = [t(x0 + cols["name"] * CW, yy, name, "w"),
             star_icon(x0 + cols["stars"] * CW + 4, yy - 5),
             t(x0 + (cols["stars"] + 1.6) * CW, yy, fmt_k(r["stargazers_count"])),
             fork_icon(x0 + cols["forks"] * CW + 4, yy - 5),
             t(x0 + (cols["forks"] + 1.6) * CW, yy, str(r["forks_count"]), "d"),
             f'<circle cx="{x0 + cols["lang"] * CW + 4:.1f}" cy="{yy - 4.5:.1f}" r="4" '
             f'fill="{LANG_COLORS.get(lang, DIM)}"/>',
             t(x0 + (cols["lang"] + 1.6) * CW, yy, lang[:10]),
             t(x0 + cols["desc"] * CW, yy, desc, "d")]
        body.append(f'<g class="r" style="animation-delay:{d:.2f}s">{"".join(g)}</g>')
    y_end = y + len(repos) * LH + 12
    body.append(idle_prompt(x0, y_end, t0 + 0.3 + len(repos) * 0.08))
    return frame(int(y_end + PAD - 4), title_for("ls", 12), "".join(body), "Top projects by stars")


def card_homelab() -> str:
    x0 = PAD
    y = BAR + PAD + 6
    body = []
    s, t0 = typed(x0, y, 'docker ps --format "table {{.Names}}\\t{{.Image}}\\t{{.Status}}"', 0.3, cps=40, uid="dk")
    body.append(s)
    y += LH + 10
    cols = (0, 23, 66)
    head = "".join(t(x0 + c * CW, y, h, "d w") for c, h in zip(cols, ("NAMES", "IMAGE", "STATUS")))
    body.append(f'<g class="r" style="animation-delay:{t0:.2f}s">{head}</g>')
    y += LH
    for i, (name, image) in enumerate(SERVICES):
        yy = y + i * LH
        d = t0 + 0.12 + i * 0.07
        g = [t(x0 + cols[0] * CW, yy, name, "w"), t(x0 + cols[1] * CW, yy, image, "d"),
             f'<circle cx="{x0 + cols[2] * CW + 4:.1f}" cy="{yy - 4.5:.1f}" r="3.8" fill="{GREEN_OK}" class="pulse" '
             f'style="animation-delay:{i * .27:.2f}s"/>',
             f'<text x="{x0 + (cols[2] + 1.6) * CW:.1f}" y="{yy:.1f}" style="fill:{GREEN_OK}">Up (healthy)</text>']
        body.append(f'<g class="r" style="animation-delay:{d:.2f}s">{"".join(g)}</g>')
    yy = y + len(SERVICES) * LH + 8
    dd = t0 + 0.2 + len(SERVICES) * 0.07
    body.append(t(x0, yy, HOMELAB_FOOTER, "d", dd))
    y_end = yy + LH + 10
    body.append(idle_prompt(x0, y_end, dd + 0.3))
    return frame(int(y_end + PAD - 4), title_for("docker", 14), "".join(body), "Self-hosted homelab services")


def card_stats(data: dict, today: dt.date) -> str:
    x0 = PAD
    y = BAR + PAD + 6
    body = []
    s, t0 = typed(x0, y, f"htop --user={USER}", 0.3, uid="ht")
    body.append(s)
    y += LH + 12

    # left: languages as htop meters, one stripe colour each
    langs = top_langs(data.get("languages", {}), 6)
    bar_chars = 24
    for i, (name, pct) in enumerate(langs):
        yy = y + i * LH
        d = t0 + i * 0.07
        fill = max(1, round(bar_chars * pct / 100)) if pct >= 0.5 else 0
        col = RAINBOW[i % 6]
        g = (f'<tspan class="c">{escape(name[:11].ljust(12))}</tspan><tspan class="w">[</tspan>'
             f'<tspan style="fill:{col}">{"|" * fill}</tspan><tspan class="f">{"·" * (bar_chars - fill)}</tspan>'
             f'<tspan class="d"> {pct:5.1f}%</tspan><tspan class="w">]</tspan>')
        body.append(f'<text x="{x0:.1f}" y="{yy:.1f}" class="r" style="animation-delay:{d:.2f}s">{g}</text>')

    rx = x0 + 49 * CW
    stars = sum(r["stargazers_count"] for r in data["repos"] if not r["fork"])
    forks = sum(r["forks_count"] for r in data["repos"] if not r["fork"])
    stats = [("Tasks", f"{data['public_repos']} repos"), ("Stars", f"{stars:,}"),
             ("Forks", f"{forks:,}"), ("Followers", f"{data['followers']:,}")]
    contrib = data.get("contrib")
    if contrib:
        cur, best = streaks(contrib["days"], today)
        yr = sum(v for k, v in contrib["days"].items()
                 if dt.date.fromisoformat(k) > today - dt.timedelta(days=365))
        stats += [("Commits", f"{contrib['commits'] + contrib['private']:,}"),
                  ("PRs/Issues", f"{contrib['prs']:,} / {contrib['issues']:,}"),
                  ("Load (1y)", f"{yr:,} contributions"),
                  ("Streak", f"{cur}d now · {best}d best")]
    for i, (k, v) in enumerate(stats):
        yy = y + i * LH
        d = t0 + 0.05 + i * 0.06
        body.append(f'<text x="{rx:.1f}" y="{yy:.1f}" class="r" style="animation-delay:{d:.2f}s">'
                    f'<tspan class="c">{escape((k + ":").ljust(12))}</tspan><tspan class="w">{escape(v)}</tspan></text>')

    y += max(len(langs), len(stats)) * LH + 6

    if contrib:  # activity graph, last 52 weeks, rainbow across the year
        wk = weekly(contrib["days"], today)
        top = max(wk) or 1
        gh, gw = 64, WW - 2 * PAD
        bw = gw / len(wk)
        y += 6
        body.append(t(x0, y, "activity · last 52 weeks", "d", t0 + 0.6))
        y += 12
        bars = [f'<linearGradient id="rbx" gradientUnits="userSpaceOnUse" x1="{x0}" y1="0" x2="{x0 + gw}" y2="0">'
                + "".join(f'<stop offset="{i / 5:.2f}" stop-color="{c}"/>' for i, c in enumerate(RAINBOW))
                + '</linearGradient>',
                f'<line x1="{x0}" y1="{y + gh + .5}" x2="{x0 + gw}" y2="{y + gh + .5}" stroke="{FAINT}"/>']
        for i, v in enumerate(wk):
            hgt = max(2.5, gh * v / top) if v else 1.5
            fill = "url(#rbx)" if v else FAINT
            bars.append(f'<rect class="grow" style="animation-delay:{t0 + .7 + i * .012:.2f}s" '
                        f'x="{x0 + i * bw + 1:.1f}" y="{y + gh - hgt:.1f}" width="{bw - 2.4:.1f}" '
                        f'height="{hgt:.1f}" rx="1.5" fill="{fill}"/>')
        body.append("".join(bars))
        y += gh + 14

    y_end = y + LH
    body.append(idle_prompt(x0, y_end, t0 + 1.4))
    return frame(int(y_end + PAD - 4), title_for("htop", 16), "".join(body), "GitHub stats")


# --------------------------------------------------------------------------
# Buttons: small clickable chips in the same style
# --------------------------------------------------------------------------
BFS = 13
BCW = BFS * 0.6


def button(label: str, detail: str = "", color: str = RAINBOW[5], stars: int | None = None) -> str:
    h, padx = 36, 14
    parts_w = 14 + len(label) * BCW + (14 + len(detail) * BCW if detail else 0)
    if stars is not None:
        parts_w += 14 + 14 + len(fmt_k(stars)) * BCW
    w = int(math.ceil(parts_w + 2 * padx))
    W2, H2 = w + 4, h + 6
    x = padx
    out = [f'<circle cx="{x + 4}" cy="{h / 2}" r="4.5" fill="{color}"/>']
    x += 14
    out.append(f'<text x="{x:.1f}" y="{h / 2 + 4.5}" class="w">{escape(label)}</text>')
    x += len(label) * BCW
    if detail:
        x += 14
        out.append(f'<text x="{x:.1f}" y="{h / 2 + 4.5}" class="d">{escape(detail)}</text>')
        x += len(detail) * BCW
    if stars is not None:
        x += 14
        out.append(star_icon(x + 5, h / 2, 5.2))
        x += 14
        out.append(f'<text x="{x:.1f}" y="{h / 2 + 4.5}" class="d">{fmt_k(stars)}</text>')
    css = (f"{_font_face()}text{{font-family:'JBM','SF Mono',Menlo,monospace;font-size:{BFS}px;"
           f"fill:{TXT};white-space:pre}}.d{{fill:{DIM}}}.w{{font-weight:700}}")
    name = f"{label} {detail}".strip()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W2}" height="{H2}" viewBox="0 0 {W2} {H2}" '
            f'role="img" aria-label="{escape(name)}"><title>{escape(name)}</title><defs><style>{css}</style>'
            f'<linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2c2c2e"/>'
            f'<stop offset="1" stop-color="{WIN}"/></linearGradient>'
            f'<filter id="s" x="-10%" y="-20%" width="120%" height="160%"><feDropShadow dx="0" dy="2" '
            f'stdDeviation="1.5" flood-opacity=".3"/></filter></defs>'
            f'<g transform="translate(2 1)"><rect width="{w}" height="{h}" rx="9" fill="url(#g)" filter="url(#s)"/>'
            f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="8.5" fill="none" stroke="{EDGE}"/>'
            f'{"".join(out)}</g></svg>\n')


STACK = [
    ["Python", "Java", "TypeScript", "JavaScript", "Vue", "React", "Spring Boot", "Flask", "Node.js"],
    ["PostgreSQL", "SQLAlchemy", "Docker", "Proxmox", "Linux", "Home Assistant", "MQTT", "GitHub Actions"],
]


def stack_svg() -> str:
    """Two centred rows of tech chips, one rainbow colour per chip."""
    h, gap, padx, rowgap = 30, 6, 10, 8
    rows, k = [], 0
    for row in STACK:
        chips = []
        for name in row:
            chips.append((name, 12 + len(name) * BCW + 2 * padx, RAINBOW[k % 6]))
            k += 1
        rows.append(chips)
    width = max(sum(c[1] for c in r) + gap * (len(r) - 1) for r in rows)
    Wt = int(math.ceil(width)) + 4
    Ht = len(rows) * h + (len(rows) - 1) * rowgap + 4
    out, i = [], 0
    for ri, chips in enumerate(rows):
        rw = sum(c[1] for c in chips) + gap * (len(chips) - 1)
        x, y = (Wt - rw) / 2, 2 + ri * (h + rowgap)
        for name, cw, col in chips:
            out.append(f'<g class="slide" style="animation-delay:{.05 * i:.2f}s">'
                       f'<rect x="{x:.1f}" y="{y}" width="{cw:.1f}" height="{h}" rx="8" fill="{WIN}" stroke="{EDGE}"/>'
                       f'<circle cx="{x + padx + 4:.1f}" cy="{y + h / 2}" r="4" fill="{col}"/>'
                       f'<text x="{x + padx + 12:.1f}" y="{y + h / 2 + 4.5}">{escape(name)}</text></g>')
            x += cw + gap
            i += 1
    css = (f"{_font_face()}text{{font-family:'JBM','SF Mono',Menlo,monospace;font-size:{BFS}px;fill:{TXT};"
           f"white-space:pre}}.slide{{opacity:0;animation:up .4s ease-out forwards}}"
           f"@keyframes up{{from{{opacity:0}}to{{opacity:1}}}}"
           f"@media (prefers-reduced-motion:reduce){{.slide{{animation:none;opacity:1}}}}")
    label = "Tech stack: " + ", ".join(sum(STACK, []))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{Wt}" height="{Ht}" viewBox="0 0 {Wt} {Ht}" '
            f'role="img" aria-label="{escape(label)}"><title>{escape(label)}</title>'
            f'<defs><style>{css}</style></defs>{"".join(out)}</svg>\n')


def buttons(data: dict) -> dict[str, str]:
    out = {}
    for i, name in enumerate(LINKED_REPOS):
        out[f"btn-{name.lower()}.svg"] = button(name, color=RAINBOW[i % 6])
    pulls = data.get("docker_pulls")
    out["btn-docker.svg"] = button(f"docker pull {DOCKER_IMAGE}",
                                   f"{fmt_k(pulls)} pulls" if pulls else "", color=RAINBOW[5])
    for i, (stem, label, detail) in enumerate(SOCIAL):
        out[f"btn-{stem}.svg"] = button(label, detail, color=RAINBOW[(i * 3 + 3) % 6])
    return out


# --------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="render from a JSON dump instead of the API")
    ap.add_argument("--dump", help="write fetched data to this JSON file")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    if args.data:
        data = json.loads(pathlib.Path(args.data).read_text())
    else:
        data = fetch(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
        if args.dump:
            pathlib.Path(args.dump).write_text(json.dumps(data))

    today = dt.datetime.now(dt.timezone.utc).date()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cards = {
        "neofetch.svg": card_neofetch(data, today),
        "projects.svg": card_projects(data),
        "homelab.svg": card_homelab(),
        "htop.svg": card_stats(data, today),
        "stack.svg": stack_svg(),
        **buttons(data),
    }
    for name, svg in cards.items():
        svg = _embed_fonts(svg)
        (out / name).write_text(svg, encoding="utf-8")
        print(f"wrote {out / name} ({len(svg) // 1024} KB)")


if __name__ == "__main__":
    main()
