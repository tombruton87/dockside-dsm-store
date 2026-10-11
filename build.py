#!/usr/bin/env python3
"""Builds the package source in site/ from each repo's latest GitHub release.

    build.py            # uses GITHUB_TOKEN / GH_TOKEN if set (higher API limits)

site/index.json  the catalog Package Center reads: {"packages": [...]}
site/meta.json   each package's os_min_ver, for the Worker to filter by DSM build
site/icons/      the icons out of each .spk
site/index.html  a page for people, with how to add the source
cache.json       what came out of each .spk, by release asset id, so an unchanged
                 package is not downloaded again

Only the standard library, so it runs the same here and in GitHub Actions."""
import hashlib, html, json, os, re, sys, tarfile, tempfile, urllib.request
from urllib.parse import quote

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE, ICONS = os.path.join(ROOT, "site"), os.path.join(ROOT, "site", "icons")
CONFIG = json.load(open(os.path.join(ROOT, "config.json")))
CACHE_FILE = os.path.join(ROOT, "cache.json")
# The mark: three containers stacked on the quay, over two waves. Page header and favicon.
LOGO = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40" aria-hidden="true">'
        '<rect x="12.5" y="5" width="15" height="8.5" rx="1.2" fill="#d6b479"/>'
        '<rect x="4" y="14.5" width="15" height="8.5" rx="1.2" fill="#3f949f"/>'
        '<rect x="21" y="14.5" width="15" height="8.5" rx="1.2" fill="#e0755a"/>'
        '<path d="M16 7.5v4M19 7.5v4M22 7.5v4M25 7.5v4M7.5 17v4M10.5 17v4M13.5 17v4M16.5 17v4M24.5 17v4M27.5 17v4M30.5 17v4M33.5 17v4" stroke="#000" stroke-opacity=".14" stroke-width="1"/>'
        '<path d="M2 29.5q4.5-3.2 9 0t9 0 9 0 9 0" fill="none" stroke="#1d6d86" stroke-width="2.4" stroke-linecap="round"/>'
        '<path d="M6.5 35q4.5-3.2 9 0t9 0 9 0" fill="none" stroke="#5db3c4" stroke-width="2.4" stroke-linecap="round"/></svg>')
SPK_NAME = re.compile(r"^([a-z0-9]+)-(\d+(?:\.\d+)*-\d+)\.spk$")   # bookstack-1.0.1-1.spk, not bookstack.spk


def api(path):
    req = urllib.request.Request("https://api.github.com/" + path, headers={"Accept": "application/vnd.github+json", "User-Agent": "dockside-dsm-store"})
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token: req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=30) as r: return json.load(r)


def read_info(text):
    info = {}
    for line in text.splitlines():
        m = re.match(r'^([A-Za-z0-9_]+)="(.*)"$', line.strip())
        if m: info[m.group(1)] = m.group(2).replace('\\"', '"')
    return info


def unpack(asset, pkg):
    """Downloads one .spk; returns what the catalog needs from it and writes its icons."""
    with tempfile.NamedTemporaryFile(suffix=".spk") as f:
        req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "dockside-dsm-store"})
        with urllib.request.urlopen(req, timeout=120) as r:
            md5 = hashlib.md5()
            for chunk in iter(lambda: r.read(1 << 16), b""): f.write(chunk); md5.update(chunk)
        f.flush(); size = f.tell()
        with tarfile.open(f.name) as t:
            names = t.getnames()
            info = read_info(t.extractfile("INFO").read().decode())
            for member, out in (("PACKAGE_ICON.PNG", "64"), ("PACKAGE_ICON_256.PNG", "256")):
                if member in names:
                    with open(os.path.join(ICONS, f"{pkg}-{out}.png"), "wb") as o: o.write(t.extractfile(member).read())
    wiz = [n.split("/", 1)[1] for n in names if n.startswith("WIZARD_UIFILES/")]
    return {"asset_id": asset["id"], "md5": md5.hexdigest(), "size": size, "info": info,
            "install_wizard": any(n.startswith("install_uifile") for n in wiz),
            "upgrade_wizard": any(n.startswith("upgrade_uifile") for n in wiz)}


def changelog_html(body):
    """A release's notes as the small HTML Package Center shows under What's new."""
    paras = []
    for para in re.split(r"\n\s*\n", (body or "").replace("\r", "").strip()):
        items = []
        for line in para.split("\n"):
            s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(line.strip(), quote=False))
            if s.startswith(("- ", "* ")): items.append("• " + s[2:])
            elif items: items[-1] += " " + s   # a wrapped line continues the one before
            else: items.append(s)
        paras.append("<br>".join(items))
    return "<br><br>".join(p for p in paras if p)


def main():
    os.makedirs(ICONS, exist_ok=True)
    cache = json.load(open(CACHE_FILE)) if os.path.exists(CACHE_FILE) else {}
    repos = [l.strip() for l in open(os.path.join(ROOT, "repos.txt")) if l.strip() and not l.startswith("#")]
    base = CONFIG.get("base_url", "").rstrip("/")
    packages, meta, failed = [], {}, []
    old = load(os.path.join(SITE, "index.json"), {"packages": []})["packages"]; old_meta = load(os.path.join(SITE, "meta.json"), {})
    for repo in repos:
        try:
            rel = api(f"repos/{repo}/releases/latest")
            spks = [a for a in rel["assets"] if SPK_NAME.match(a["name"])]
            if len(spks) != 1: raise ValueError(f"expected one versioned .spk in {rel['tag_name']}, found {[a['name'] for a in spks]}")
            asset = spks[0]; pkg, version = SPK_NAME.match(asset["name"]).groups()
            got = cache.get(repo)
            if not got or got["asset_id"] != asset["id"] or not os.path.exists(os.path.join(ICONS, f"{pkg}-256.png")):
                print(f"{repo}: reading {asset['name']}", file=sys.stderr)
                got = cache[repo] = unpack(asset, pkg)
            info = got["info"]
            if info.get("package") != pkg or info.get("version") != version:
                raise ValueError(f"{asset['name']} says package={info.get('package')} version={info.get('version')}")
            icon = lambda s: f"{base}/icons/{pkg}-{s}.png" if base else f"icons/{pkg}-{s}.png"
            entry = {
                "package": pkg, "version": version,
                "dname": info.get("displayname", pkg), "desc": info.get("description", ""),
                "link": asset["browser_download_url"],
                "thumbnail": [icon(64), icon(256)], "thumbnail_retina": [icon(256), icon(256)], "snapshot": [],
                # Quick install/upgrade only without a wizard, as SynoCommunity's spkrepo does it.
                "qinst": not got["install_wizard"], "qupgrade": not got["upgrade_wizard"], "qstart": not got["install_wizard"],
                "deppkgs": info.get("install_dep_packages") or None, "conflictpkgs": info.get("install_conflict_packages") or None,
                "download_count": 0, "recent_download_count": 0,   # counts would make every hourly build a new commit
                "changelog": changelog_html(rel.get("body")),
                "maintainer": info.get("maintainer"), "maintainer_url": info.get("maintainer_url"),
                "distributor": CONFIG.get("distributor"), "distributor_url": CONFIG.get("distributor_url"),
                "support_url": info.get("support_url"),
                "md5": got["md5"], "size": got["size"],
            }
            packages.append({k: v for k, v in entry.items() if v is not None and v != ""})
            meta[pkg] = {"os_min_ver": info.get("os_min_ver", ""), "repo": repo, "tag": rel["tag_name"]}
        except Exception as e:   # a failed lookup keeps the package's last good entry; it never drops off the feed
            prev = [k for k, v in old_meta.items() if v.get("repo") == repo]
            packages += [p for p in old if p["package"] in prev]; meta.update({k: old_meta[k] for k in prev})
            warn(f"{repo}: {e} — " + ("kept the previous entry" if prev else "not listed"))
            failed.append(repo)
    packages.sort(key=lambda p: p["dname"].lower())
    write(os.path.join(SITE, "index.json"), json.dumps({"packages": packages}, indent=1, ensure_ascii=False) + "\n")
    write(os.path.join(SITE, "meta.json"), json.dumps(meta, indent=1, sort_keys=True) + "\n")
    write(os.path.join(SITE, "index.html"), page(packages, meta))
    json.dump({r: cache[r] for r in repos if r in cache}, open(CACHE_FILE, "w"), indent=1, sort_keys=True)
    for name in os.listdir(ICONS):   # icons of packages no longer listed
        if name.rsplit("-", 1)[0] not in meta: os.remove(os.path.join(ICONS, name))
    print(f"{len(packages)} packages" + (f", {len(failed)} skipped: {' '.join(failed)}" if failed else ""), file=sys.stderr)
    return 1 if failed else 0


def load(path, default):
    try: return json.load(open(path))
    except (OSError, ValueError): return default


def warn(msg):
    print(("::warning::" if os.environ.get("GITHUB_ACTIONS") else "warning: ") + msg, file=sys.stderr)


def write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as f: f.write(text)
    os.replace(tmp, path)


def page(packages, meta):
    """The page for people: a shell that loads index.json and lists it, with a search.
    The package names are also written in, for anyone without JavaScript."""
    e = html.escape
    source = (CONFIG.get("base_url") or "").rstrip("/") + "/"   # "/" alone: filled in from the page's own address
    plain = "".join(f'<li><a href="{e(p["link"])}">{e(p["dname"])} {e(p["version"].rsplit("-", 1)[0])}</a></li>' for p in packages)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(CONFIG['title'])}</title>
<meta name="description" content="{e(CONFIG['tagline'])}">
<link rel="icon" href="data:image/svg+xml,{quote(LOGO)}">
<style>
  :root {{ --bg: #f2f6f9; --card: #fff; --text: #13283b; --muted: #556b7f; --line: #d8e2ea; --link: #1d6d86; --accent: #1d6d86; --chip: #e7f0f4;
          --coral: #e0755a; --sand: #d6b479; --teal: #3f949f; --slate: #4f6d8f; --veil: rgba(242, 246, 249, .6); --sky: #cfe3ef;
          --art: url(art/harbour-day-1200.webp); --art-wide: url(art/harbour-day-2400.webp); }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg: #0a1520; --card: #102030; --text: #e2ecf4; --muted: #93a8bb; --line: #1d3245; --link: #7cc7d6; --accent: #5db3c4; --chip: #17293b;
          --veil: rgba(10, 21, 32, .5); --sky: #14244a; --art: url(art/harbour-night-1200.webp); --art-wide: url(art/harbour-night-2400.webp); }} }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--text); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  a {{ color: var(--link); }}
  .wrap {{ max-width: 1180px; margin: 0 auto; padding: 0 16px; }}

  /* The harbour: the picture fades into the page at the bottom and behind the title on the left. */
  .hero {{ position: relative; background: var(--sky) var(--art) no-repeat 80% 100% / cover; }}
  @media (min-width: 900px) {{ .hero {{ background-image: var(--art-wide); }} }}
  .hero::after {{ content: ""; position: absolute; inset: 0; pointer-events: none;
          background: linear-gradient(90deg, var(--veil) 0%, transparent 55%), linear-gradient(180deg, transparent 62%, var(--bg) 100%); }}
  .hero .wrap {{ position: relative; z-index: 1; min-height: clamp(300px, 30vw, 420px); padding-top: 44px; padding-bottom: 44px; display: flex; flex-direction: column; justify-content: center; }}
  .brand {{ display: flex; align-items: center; gap: 14px; }}
  .brand svg {{ flex: none; width: 52px; height: 52px; filter: drop-shadow(0 1px 2px rgba(0, 0, 0, .12)); }}
  h1 {{ font-size: clamp(28px, 4vw, 40px); line-height: 1.1; margin: 0; letter-spacing: -0.015em; }}
  .lead {{ margin: 10px 0 22px; max-width: 46ch; font-size: 16px; }}
  .bar {{ display: flex; align-items: center; gap: 12px; }}
  .bar input {{ flex: 1; max-width: 440px; font: inherit; padding: 11px 14px 11px 40px; border: 1px solid var(--line); border-radius: 10px; color: var(--text);
          background: var(--card) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' fill='none' stroke='%238894a2' stroke-width='2'%3E%3Ccircle cx='7' cy='7' r='5'/%3E%3Cpath d='m11 11 4 4'/%3E%3C/svg%3E") no-repeat 14px center;
          box-shadow: 0 2px 10px rgba(16, 40, 60, .08); }}
  .bar input:focus {{ outline: 2px solid var(--accent); outline-offset: -1px; border-color: transparent; }}
  #count {{ font-size: 14px; color: var(--muted); }}
  @media (max-width: 700px) {{ .hero .wrap {{ justify-content: flex-start; padding-top: 28px; padding-bottom: 170px; }} .lead {{ margin-bottom: 16px; }} }}

  /* Getting it into Package Center: three steps, numbered in life rings. */
  .how {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px 24px; background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px 20px; margin: 4px 0 22px; font-size: 14px; }}
  .how h2 {{ grid-column: 1 / -1; font-size: 14px; margin: 0; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: .06em; }}
  .how .step {{ display: flex; gap: 10px; align-items: flex-start; }}
  .ring {{ flex: none; width: 28px; height: 28px; border-radius: 50%; display: grid; place-items: center; margin-top: -2px;
          background: conic-gradient(from 45deg, var(--coral) 0 25%, #fbf6ee 0 50%, var(--coral) 0 75%, #fbf6ee 0); box-shadow: 0 0 0 1.5px #b9563f; }}
  .ring span {{ width: 15px; height: 15px; border-radius: 50%; background: var(--card); box-shadow: 0 0 0 1.5px #b9563f; display: grid; place-items: center; font-size: 10px; font-weight: 700; }}
  @media (max-width: 760px) {{ .how {{ grid-template-columns: 1fr; }} }}
  code {{ background: var(--chip); border-radius: 4px; padding: 1px 6px; word-break: break-all; font-size: 13px; }}

  /* The apps: each card topped like a container, versions on cargo tags. */
  .grid {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); }}
  .card[hidden] {{ display: none; }}
  .card {{ --stripe: var(--teal); display: flex; flex-direction: column; background: var(--card); border: 1px solid var(--line); border-top: 4px solid var(--stripe); border-radius: 6px 6px 10px 10px; padding: 16px;
          transition: transform .15s ease, box-shadow .15s ease; }}
  .card:nth-child(4n+2) {{ --stripe: var(--coral); }} .card:nth-child(4n+3) {{ --stripe: var(--sand); }} .card:nth-child(4n+4) {{ --stripe: var(--slate); }}
  .card:hover {{ transform: translateY(-2px); box-shadow: 0 8px 22px rgba(16, 40, 60, .10); }}
  @media (prefers-reduced-motion: reduce) {{ .card {{ transition: none; }} .card:hover {{ transform: none; }} }}
  .card .top {{ display: flex; gap: 12px; align-items: center; margin-bottom: 10px; }}
  .card img {{ flex: none; width: 48px; height: 48px; border-radius: 11px; }}
  .card h2 {{ font-size: 16px; margin: 0; line-height: 1.3; }}
  .card .ver {{ position: relative; display: inline-block; margin-top: 3px; background: var(--chip); color: var(--muted); border-radius: 3px 10px 10px 3px; padding: 0 8px 0 15px; font-size: 12px; }}
  .card .ver::before {{ content: ""; position: absolute; left: 5px; top: 50%; width: 5px; height: 5px; margin-top: -2.5px; border-radius: 50%; background: var(--card); box-shadow: inset 0 0 0 1px var(--muted); opacity: .7; }}
  .card p {{ margin: 0 0 12px; color: var(--muted); font-size: 14px; flex: 1; display: -webkit-box; -webkit-line-clamp: 5; -webkit-box-orient: vertical; overflow: hidden; }}
  .card .links {{ display: flex; gap: 14px; font-size: 14px; }}
  .card .links a {{ text-decoration: none; }} .card .links a:hover {{ text-decoration: underline; }}
  mark {{ background: rgba(224, 117, 90, .28); color: inherit; border-radius: 2px; }}
  .empty {{ color: var(--muted); padding: 32px 0; text-align: center; }}
  footer {{ color: var(--muted); font-size: 13px; padding: 36px 0 44px; text-align: center; }}
  footer svg {{ display: block; margin: 0 auto 10px; opacity: .5; }}
</style></head><body>
<header class="hero"><div class="wrap">
  <div class="brand">{LOGO}<h1>{e(CONFIG['title'])}</h1></div>
  <p class="lead">{e(CONFIG['tagline'])}</p>
  <div class="bar"><input id="q" type="search" placeholder="Search apps" aria-label="Search apps" autocomplete="off"><span id="count" aria-live="polite"></span></div>
</div></header>
<main class="wrap">
<section class="how" aria-label="Add to Package Center"><h2>Moor it in Package Center</h2>
  <div class="step"><span class="ring"><span>1</span></span><div><b>Package Center</b> → <b>Settings</b> → <b>Package Sources</b> → <b>Add</b></div></div>
  <div class="step"><span class="ring"><span>2</span></span><div>Name <code>{e(CONFIG['source_name'])}</code><br>Location <code id="src">{e(source)}</code></div></div>
  <div class="step"><span class="ring"><span>3</span></span><div>The apps appear under <b>Community</b>, and their updates arrive there too.</div></div>
</section>
<ul class="grid" id="grid"></ul>
<p class="empty" id="empty" hidden>Nothing in the harbour matches that.</p>
<noscript><ul>{plain}</ul></noscript>
</main>
<footer class="wrap"><svg width="64" height="10" viewBox="0 0 64 10" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M0 5q4-4 8 0t8 0 8 0 8 0 8 0 8 0 8 0 8 0"/></svg>Each app downloads straight from its own GitHub release.</footer>
<script>
(function () {{
  var src = document.getElementById("src"); if (src.textContent === "/") src.textContent = location.origin + "/";
  var q = document.getElementById("q"), grid = document.getElementById("grid"), count = document.getElementById("count"), empty = document.getElementById("empty"), all = [];
  function el(tag, cls, text) {{ var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }}
  function marked(text, words) {{   // text with each search word highlighted, built as nodes (never innerHTML)
    var frag = document.createDocumentFragment(); if (!words.length) {{ frag.appendChild(document.createTextNode(text)); return frag; }}
    var re = new RegExp("(" + words.map(function (w) {{ return w.replace(/[.*+?^${{}}()|[\\]\\\\]/g, "\\\\$&"); }}).join("|") + ")", "ig");
    text.split(re).forEach(function (part, i) {{ frag.appendChild(i % 2 ? el("mark", null, part) : document.createTextNode(part)); }});
    return frag;
  }}
  function card(p) {{   // made once per app; a search only hides it and re-marks its text
    var li = el("li", "card"), top = el("div", "top"), img = el("img"), name = el("div"), h = el("h2"), d = el("p");
    img.src = p.thumbnail[p.thumbnail.length - 1]; img.alt = ""; img.width = img.height = 48;
    name.appendChild(h); name.appendChild(el("span", "ver", p.version.replace(/-\\d+$/, "")));
    top.appendChild(img); top.appendChild(name); li.appendChild(top); d.title = p.desc; li.appendChild(d);
    var links = el("div", "links"), a = el("a", null, "Source and notes"), b = el("a", null, "Download .spk");
    a.href = p.maintainer_url || "#"; b.href = p.link; links.appendChild(a); links.appendChild(b); li.appendChild(links);
    grid.appendChild(li);
    return {{ p: p, li: li, h: h, d: d, hay: (p.dname + " " + p.package + " " + p.desc).toLowerCase() }};
  }}
  function show() {{
    var words = q.value.trim().toLowerCase().split(/\\s+/).filter(Boolean), hits = 0;
    all.forEach(function (c) {{
      var hit = words.every(function (w) {{ return c.hay.indexOf(w) >= 0; }});
      c.li.hidden = !hit; if (!hit) return; hits++;
      c.h.replaceChildren(marked(c.p.dname, words)); c.d.replaceChildren(marked(c.p.desc, words));
    }});
    count.textContent = words.length ? hits + " of " + all.length + " apps" : all.length + " apps";
    empty.hidden = hits > 0;
    try {{ history.replaceState(null, "", words.length ? "?q=" + encodeURIComponent(q.value.trim()) : location.pathname); }} catch (e) {{}}
  }}
  q.value = new URLSearchParams(location.search).get("q") || "";
  q.addEventListener("input", show);
  document.addEventListener("keydown", function (ev) {{ if (ev.key === "/" && document.activeElement !== q) {{ ev.preventDefault(); q.focus(); }} }});
  fetch("index.json", {{ headers: {{ Accept: "application/json" }} }})
    .then(function (r) {{ if (!r.ok) throw new Error(r.status); return r.json(); }})
    .then(function (d) {{ all = (d.packages || []).map(card); show(); }})
    .catch(function () {{ count.textContent = "Couldn't load the list — try reloading."; }});
}})();
</script>
</body></html>
"""

if __name__ == "__main__":
    sys.exit(main())
