#!/usr/bin/env python3
"""Generate the phosphor-green terminal SVGs used by the profile README.

Pure standard library. Data comes from the GitHub REST API (profile, repos,
languages) and, when a token is available, the GraphQL API (commits, PRs,
issues, contribution calendar). Anything that cannot be fetched is simply
left out of the cards instead of being invented.

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
LANG_EXCLUDE = {"HTML", "CSS", "SCSS", "Less", "Dockerfile", "Makefile",
                "Procfile", "Batchfile", "Jinja", "Smarty"}
PROJECT_EXCLUDE = {USER}  # the profile repo itself
PROJECTS_SHOWN = 6

# The homelab is described, not measured: no fake uptimes or ports.
SERVICES = [
    ("suggestarr", "ciuse99/suggestarr"),
    ("jellyfin", "jellyfin/jellyfin"),
    ("home-assistant", "ghcr.io/home-assistant/home-assistant"),
    ("immich-server", "ghcr.io/immich-app/immich-server"),
    ("vaultwarden", "vaultwarden/server"),
    ("pihole", "pihole/pihole"),
    ("uptime-kuma", "louislam/uptime-kuma"),
    ("nginx-proxy-manager", "jc21/nginx-proxy-manager"),
]
HOMELAB_FOOTER = "# host: Proxmox VE · router: GL.iNet · dns: Pi-hole · all self-hosted"

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT.parent / "profile"

# --------------------------------------------------------------------------
# Palette / metrics
# --------------------------------------------------------------------------
BG = "#030a05"
PANEL = "#06120a"
EDGE = "#164d26"
FAINT = "#1d5c31"
DIM = "#2f9e55"
MAIN = "#39ff6a"
BRIGHT = "#b8ffca"
FS = 14                 # font size (px)
CW = FS * 0.6           # JetBrains Mono advance = 600/1000 em
LH = 22                 # line height
PAD = 26                # inner padding
BAR = 34                # title bar height
W = 860                 # card width


# --------------------------------------------------------------------------
# GitHub data
# --------------------------------------------------------------------------
def _req(url: str, token: str | None, body: dict | None = None):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"{USER}-profile-gen"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


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
        "repos": [
            {k: r[k] for k in ("name", "description", "language", "stargazers_count",
                               "forks_count", "fork", "archived", "html_url")}
            for r in repos
        ],
        "languages": langs,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
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
    if n >= 1000:
        s = f"{n / 1000:.1f}".rstrip("0").rstrip(".")
        return f"{s}k"
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
def _font_face() -> str:
    out = []
    for weight, name in ((400, "Regular"), (700, "Bold")):
        b64 = base64.b64encode((ROOT / "fonts" / f"JetBrainsMonoNL-{name}.subset.woff2").read_bytes()).decode()
        out.append(f"@font-face{{font-family:'JBM';font-weight:{weight};"
                   f"src:url(data:font/woff2;base64,{b64}) format('woff2')}}")
    return "".join(out)


CSS = f"""
text{{font-family:'JBM','JetBrains Mono',Menlo,Consolas,monospace;font-size:{FS}px;fill:{MAIN};white-space:pre}}
.b{{fill:{BRIGHT}}} .d{{fill:{DIM}}} .f{{fill:{FAINT}}} .w{{font-weight:700}}
.r{{opacity:0;animation:on .01s linear forwards}}
@keyframes on{{to{{opacity:1}}}}
.cur{{animation:blink 1.05s steps(1) infinite}}
@keyframes blink{{50%{{opacity:0}}}}
.scr{{animation:flick 7s infinite}}
@keyframes flick{{0%,100%{{opacity:1}}48%{{opacity:1}}49%{{opacity:.93}}50%{{opacity:1}}87%{{opacity:.97}}}}
.grow{{transform-box:fill-box;transform-origin:50% 100%;transform:scaleY(0);animation:grow .5s cubic-bezier(.2,.8,.2,1) forwards}}
@keyframes grow{{to{{transform:scaleY(1)}}}}
.pulse{{animation:pulse 2s ease-in-out infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.35}}}}
@media (prefers-reduced-motion:reduce){{.r,.grow,.scr,.pulse{{animation:none;opacity:1;transform:none}}}}
"""


def t(x: float, y: float, s: str, cls: str = "", delay: float | None = None, extra: str = "") -> str:
    c = cls + (" r" if delay is not None else "")
    st = f' style="animation-delay:{delay:.2f}s"' if delay is not None else ""
    return f'<text x="{x:.1f}" y="{y:.1f}" class="{c.strip()}"{st}{extra}>{escape(s)}</text>'


def prompt_spans(path: str = "~") -> str:
    return (f'<tspan class="b w">{SHORT}@{HOST}</tspan><tspan class="d">:</tspan>'
            f'<tspan class="b">{escape(path)}</tspan><tspan class="d">$ </tspan>')


PROMPT_LEN = len(f"{SHORT}@{HOST}:~$ ")


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
        f'<text x="{cx:.1f}" y="{y:.1f}" class="b" clip-path="url(#{uid})">{escape(cmd)}</text>'
        f'<g opacity="1"><set attributeName="opacity" to="0" begin="{end + .15:.2f}s" fill="freeze"/>'
        f'<rect class="cur" x="{cx:.1f}" y="{y - FS + 2:.1f}" width="{CW:.1f}" height="{FS + 3}" fill="{MAIN}">'
        f'<animate attributeName="x" values="{xs}" calcMode="discrete" begin="{begin:.2f}s" dur="{dur:.2f}s" fill="freeze"/>'
        f'</rect></g>'
    )
    return svg, end + 0.25


def idle_prompt(x: float, y: float, delay: float) -> str:
    cx = x + PROMPT_LEN * CW
    return (f'<g class="r" style="animation-delay:{delay:.2f}s">'
            f'<text x="{x:.1f}" y="{y:.1f}">{prompt_spans()}</text>'
            f'<rect class="cur" x="{cx:.1f}" y="{y - FS + 2:.1f}" width="{CW:.1f}" height="{FS + 3}" fill="{MAIN}"/></g>')


def frame(height: int, title: str, body: str, label: str) -> str:
    """Terminal window with CRT glow, scanlines and vignette."""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" viewBox="0 0 {W} {height}" role="img" aria-label="{escape(label)}">
<title>{escape(label)}</title>
<defs>
<style>{_font_face()}{CSS}</style>
<filter id="glow" x="-5%" y="-5%" width="110%" height="110%"><feGaussianBlur stdDeviation="1.4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
<pattern id="scan" width="4" height="3" patternUnits="userSpaceOnUse"><rect width="4" height="1" fill="#000" opacity=".35"/></pattern>
<radialGradient id="vig" cx="50%" cy="50%" r="75%"><stop offset="60%" stop-color="#000" stop-opacity="0"/><stop offset="100%" stop-color="#000" stop-opacity=".55"/></radialGradient>
<linearGradient id="sheen" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{MAIN}" stop-opacity=".06"/><stop offset=".5" stop-color="{MAIN}" stop-opacity="0"/></linearGradient>
<clipPath id="win"><rect width="{W}" height="{height}" rx="12"/></clipPath>
</defs>
<g clip-path="url(#win)">
<rect width="{W}" height="{height}" fill="{BG}"/>
<rect width="{W}" height="{BAR}" fill="{PANEL}"/>
<line x1="0" y1="{BAR}" x2="{W}" y2="{BAR}" stroke="{EDGE}"/>
<circle cx="20" cy="{BAR / 2}" r="5.5" fill="none" stroke="{DIM}"/>
<circle cx="38" cy="{BAR / 2}" r="5.5" fill="none" stroke="{DIM}"/>
<circle cx="56" cy="{BAR / 2}" r="5.5" fill="{MAIN}" class="pulse"/>
<text x="{W / 2}" y="{BAR / 2 + 5}" text-anchor="middle" class="d" style="font-size:12px">{escape(title)}</text>
<g class="scr" filter="url(#glow)">{body}</g>
<rect y="{BAR}" width="{W}" height="{height - BAR}" fill="url(#sheen)"/>
<rect y="{BAR}" width="{W}" height="{height - BAR}" fill="url(#scan)"/>
<rect width="{W}" height="{height}" fill="url(#vig)"/>
</g>
<rect x=".5" y=".5" width="{W - 1}" height="{height - 1}" rx="12" fill="none" stroke="{EDGE}"/>
</svg>
'''


def star_icon(x: float, y: float, r: float = 5.2) -> str:
    import math
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append(f"{x + rr * math.cos(a):.1f},{y + rr * math.sin(a):.1f}")
    return f'<polygon points="{" ".join(pts)}" fill="{MAIN}"/>'


def fork_icon(x: float, y: float) -> str:
    return (f'<g fill="none" stroke="{DIM}" stroke-width="1.3">'
            f'<circle cx="{x - 3.2:.1f}" cy="{y - 4:.1f}" r="1.5"/><circle cx="{x + 3.2:.1f}" cy="{y - 4:.1f}" r="1.5"/>'
            f'<circle cx="{x:.1f}" cy="{y + 4.5:.1f}" r="1.5"/>'
            f'<path d="M{x - 3.2:.1f} {y - 2.5:.1f}v1.5q0 2 3.2 2t3.2-2v-1.5M{x:.1f} {y + 1:.1f}v2"/></g>')


# --------------------------------------------------------------------------
# Cards
# --------------------------------------------------------------------------
ART_GB = [
    " ██████╗ ██████╗ ",
    "██╔════╝ ██╔══██╗",
    "██║  ███╗██████╔╝",
    "██║   ██║██╔══██╗",
    "╚██████╔╝██████╔╝",
    " ╚═════╝ ╚═════╝ ",
]


def ascii_computer() -> list[tuple[str, str]]:
    """(text, class) rows: a little CRT with the monogram on screen."""
    iw = 19
    rows = [(".-" + "-" * (iw + 4) + "-.", "d"),
            ("| .-" + "-" * iw + "-. |", "d")]
    rows.append(("| | " + " " * iw + " | |", "d"))
    for line in ART_GB:
        rows.append(("| | " + f" {line} ".ljust(iw) + " | |", "art"))
    rows.append(("| | " + "> sudo make coffee".ljust(iw) + " | |", "cmd"))
    rows.append(("| '-" + "-" * iw + "-' |", "d"))
    rows.append(("|" + "   ___ ___ ___    [o]".ljust(iw + 6) + "|", "d"))
    rows.append(("'" + "-" * (iw + 6) + "'", "d"))
    rows.append(("      __|" + "_" * (iw - 6) + "|__", "d"))
    rows.append(("   /" + " " + ":" * (iw - 1) + " " + "\\", "f"))
    return rows


def card_neofetch(data: dict, today: dt.date) -> str:
    x0 = PAD
    y = BAR + PAD + 8
    body, uid = [], "nf"
    s, t0 = typed(x0, y, "neofetch", 0.35, uid=uid)
    body.append(s)
    y += LH + 12

    # left: ascii computer
    art = ascii_computer()
    for i, (row, kind) in enumerate(art):
        yy = y + i * LH
        d = t0 + i * 0.045
        if kind == "art":
            # frame dim, monogram bright
            pre, mid, post = row[:4], row[4:-4], row[-4:]
            body.append(f'<text x="{x0:.1f}" y="{yy:.1f}" class="r" style="animation-delay:{d:.2f}s">'
                        f'<tspan class="d">{escape(pre)}</tspan><tspan class="b">{escape(mid)}</tspan>'
                        f'<tspan class="d">{escape(post)}</tspan></text>')
        elif kind == "cmd":
            pre, mid, post = row[:4], row[4:-4], row[-4:]
            body.append(f'<text x="{x0:.1f}" y="{yy:.1f}" class="r" style="animation-delay:{d:.2f}s">'
                        f'<tspan class="d">{escape(pre)}</tspan><tspan>{escape(mid.rstrip())}</tspan>'
                        f'<tspan class="cur">_</tspan><tspan>{" " * (len(mid) - len(mid.rstrip()) - 1)}</tspan>'
                        f'<tspan class="d">{escape(post)}</tspan></text>')
        else:
            body.append(t(x0, yy, row, kind, d))

    # right: info
    ix = x0 + (len(art[0][0]) + 5) * CW
    kx = 11  # key column width in chars
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
        info.append(("Flagship", f"{flag['name']} · {fmt_k(flag['stargazers_count'])} stars, "
                                 f"{flag['forks_count']} forks"))

    d = t0 + 0.15
    body.append(f'<text x="{ix:.1f}" y="{y:.1f}" class="r" style="animation-delay:{d:.2f}s">'
                f'<tspan class="b w">{SHORT}</tspan><tspan class="d">@</tspan>'
                f'<tspan class="b w">{HOST}</tspan></text>')
    body.append(t(ix, y + LH, "─" * len(f"{SHORT}@{HOST}"), "d", d))
    for i, (k, v) in enumerate(info):
        yy = y + (i + 2) * LH
        dd = d + 0.08 * (i + 1)
        body.append(t(ix, yy, k, "b w", dd))
        body.append(t(ix + kx * CW, yy, v, "", dd))

    # neofetch colour blocks
    yy = y + (len(info) + 3) * LH
    dd = d + 0.08 * (len(info) + 2)
    shades = ["#04140a", "#0b3a1a", "#106b2e", "#1d9e46", "#2fd15e", "#39ff6a", "#8dffaa", "#d8ffe2"]
    blocks = "".join(f'<rect x="{ix + i * 3 * CW:.1f}" y="{yy - FS + 1:.1f}" width="{3 * CW:.1f}" '
                     f'height="{FS + 4}" fill="{c}"/>' for i, c in enumerate(shades))
    body.append(f'<g class="r" style="animation-delay:{dd:.2f}s">{blocks}</g>')

    rows = max(len(art), len(info) + 4)
    y_end = y + rows * LH + 10
    body.append(idle_prompt(x0, y_end, dd + 0.3))
    h = int(y_end + PAD)
    return frame(h, f"{SHORT}@{HOST}: ~ — neofetch", "".join(body), f"{SHORT}@{HOST} — neofetch")


def card_projects(data: dict) -> str:
    x0 = PAD
    y = BAR + PAD + 8
    repos = sorted(own_repos(data), key=lambda r: (-r["stargazers_count"], r["name"].lower()))[:PROJECTS_SHOWN]
    body = []
    s, t0 = typed(x0, y, f"ls -l ~/projects --sort=stars | head -{len(repos)}", 0.3, uid="pj")
    body.append(s)
    y += LH + 10
    cols = {"name": 0, "stars": 27, "forks": 35, "lang": 42, "desc": 54}
    body.append(t(x0, y, f"total {len(own_repos(data))}", "d", t0))
    y += LH
    max_desc = int((W - 2 * PAD) / CW) - cols["desc"]
    for i, r in enumerate(repos):
        yy = y + i * LH
        d = t0 + 0.1 + i * 0.09
        desc = (r["description"] or "").strip().replace("\n", " ")
        if len(desc) > max_desc:
            desc = desc[: max_desc - 1].rstrip() + "…"
        name = r["name"] if len(r["name"]) <= 25 else r["name"][:24] + "…"
        g = [t(x0 + cols["name"] * CW, yy, name, "b w")]
        g.append(star_icon(x0 + cols["stars"] * CW + 4, yy - 5))
        g.append(t(x0 + (cols["stars"] + 1.6) * CW, yy, fmt_k(r["stargazers_count"]), ""))
        g.append(fork_icon(x0 + cols["forks"] * CW + 4, yy - 5))
        g.append(t(x0 + (cols["forks"] + 1.6) * CW, yy, str(r["forks_count"]), "d"))
        g.append(t(x0 + cols["lang"] * CW, yy, (r["language"] or "-")[:11], ""))
        g.append(t(x0 + cols["desc"] * CW, yy, desc, "d"))
        body.append(f'<g class="r" style="animation-delay:{d:.2f}s">{"".join(g)}</g>')
    y_end = y + len(repos) * LH + 12
    body.append(idle_prompt(x0, y_end, t0 + 0.3 + len(repos) * 0.09))
    return frame(int(y_end + PAD), f"{SHORT}@{HOST}: ~/projects", "".join(body), "Top projects by stars")


def card_homelab() -> str:
    x0 = PAD
    y = BAR + PAD + 8
    body = []
    s, t0 = typed(x0, y, 'docker ps --format "table {{.Names}}\\t{{.Image}}\\t{{.Status}}"', 0.3, cps=40, uid="dk")
    body.append(s)
    y += LH + 10
    cols = (0, 23, 66)
    head = [t(x0 + cols[0] * CW, y, "NAMES", "b w"), t(x0 + cols[1] * CW, y, "IMAGE", "b w"),
            t(x0 + cols[2] * CW, y, "STATUS", "b w")]
    body.append(f'<g class="r" style="animation-delay:{t0:.2f}s">{"".join(head)}</g>')
    y += LH
    for i, (name, image) in enumerate(SERVICES):
        yy = y + i * LH
        d = t0 + 0.12 + i * 0.08
        g = [t(x0 + cols[0] * CW, yy, name, ""), t(x0 + cols[1] * CW, yy, image, "d"),
             f'<circle cx="{x0 + cols[2] * CW + 4:.1f}" cy="{yy - 4.5:.1f}" r="3.6" fill="{MAIN}" class="pulse" '
             f'style="animation-delay:{i * .25:.2f}s"/>',
             t(x0 + (cols[2] + 1.5) * CW, yy, "Up (healthy)", "")]
        body.append(f'<g class="r" style="animation-delay:{d:.2f}s">{"".join(g)}</g>')
    yy = y + len(SERVICES) * LH + 8
    dd = t0 + 0.2 + len(SERVICES) * 0.08
    body.append(t(x0, yy, HOMELAB_FOOTER, "f", dd))
    y_end = yy + LH + 10
    body.append(idle_prompt(x0, y_end, dd + 0.3))
    return frame(int(y_end + PAD), f"root@{HOST}: /opt/stacks", "".join(body), "Self-hosted homelab services")


def card_stats(data: dict, today: dt.date) -> str:
    x0 = PAD
    y = BAR + PAD + 8
    body = []
    s, t0 = typed(x0, y, f"htop --user={USER}", 0.3, uid="ht")
    body.append(s)
    y += LH + 12

    # left: languages as htop CPU meters
    langs = top_langs(data.get("languages", {}), 6)
    bar_chars = 24
    for i, (name, pct) in enumerate(langs):
        yy = y + i * LH
        d = t0 + i * 0.08
        fill = round(bar_chars * pct / 100)
        label = name[:11].ljust(12)
        pct_s = f"{pct:5.1f}%"
        g = (f'<tspan class="b">{escape(label)}</tspan><tspan class="d">[</tspan>'
             f'<tspan>{"|" * fill}</tspan><tspan class="f">{"·" * (bar_chars - fill)}</tspan>'
             f'<tspan class="b"> {pct_s}</tspan><tspan class="d">]</tspan>')
        body.append(f'<text x="{x0:.1f}" y="{yy:.1f}" class="r" style="animation-delay:{d:.2f}s">{g}</text>')

    # right: counters
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
        d = t0 + 0.05 + i * 0.07
        body.append(f'<text x="{rx:.1f}" y="{yy:.1f}" class="r" style="animation-delay:{d:.2f}s">'
                    f'<tspan class="d">{escape((k + ":").ljust(12))}</tspan><tspan class="b w">{escape(v)}</tspan></text>')

    y += max(len(langs), len(stats)) * LH + 6

    # activity graph: last 52 weeks
    if contrib:
        wk = weekly(contrib["days"], today)
        top = max(wk) or 1
        gh = 64
        gw = W - 2 * PAD
        bw = gw / len(wk)
        y += 6
        body.append(t(x0, y, "activity · last 52 weeks", "d", t0 + 0.6))
        y += 10
        bars = [f'<line x1="{x0}" y1="{y + gh + .5}" x2="{x0 + gw}" y2="{y + gh + .5}" stroke="{FAINT}"/>']
        for i, v in enumerate(wk):
            hgt = max(2.0, gh * v / top) if v else 1.5
            col = MAIN if v else FAINT
            bars.append(f'<rect class="grow" style="animation-delay:{t0 + .7 + i * .012:.2f}s" '
                        f'x="{x0 + i * bw + 1:.1f}" y="{y + gh - hgt:.1f}" width="{bw - 2.2:.1f}" '
                        f'height="{hgt:.1f}" rx="1" fill="{col}"/>')
        body.append("".join(bars))
        y += gh + 14

    y_end = y + LH
    body.append(idle_prompt(x0, y_end, t0 + 1.4))
    return frame(int(y_end + PAD), f"{SHORT}@{HOST}: htop", "".join(body), "GitHub stats")


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
    }
    for name, svg in cards.items():
        (out / name).write_text(svg, encoding="utf-8")
        print(f"wrote {out / name} ({len(svg) // 1024} KB)")


if __name__ == "__main__":
    main()
