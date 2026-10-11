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

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE, ICONS = os.path.join(ROOT, "site"), os.path.join(ROOT, "site", "icons")
CONFIG = json.load(open(os.path.join(ROOT, "config.json")))
CACHE_FILE = os.path.join(ROOT, "cache.json")
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
<link rel="icon" href="icons/{e(packages[0]['package'] if packages else '')}-64.png">
<style>
  :root {{ --bg: #f4f6f9; --card: #fff; --text: #1f2933; --muted: #5f6b7a; --line: #dde3ea; --link: #0a66c2; --accent: #0a66c2; --chip: #eef2f6; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg: #12161b; --card: #1b2128; --text: #e6eaef; --muted: #9aa5b1; --line: #2b333c; --link: #6cb4ff; --accent: #4c9dff; --chip: #232b34; }} }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--text); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  a {{ color: var(--link); }}
  main {{ max-width: 1180px; margin: 0 auto; padding: 32px 16px 56px; }}
  header {{ display: flex; flex-wrap: wrap; align-items: end; justify-content: space-between; gap: 16px 32px; margin-bottom: 20px; }}
  h1 {{ font-size: 28px; margin: 0; letter-spacing: -0.01em; }}
  .lead {{ color: var(--muted); margin: 4px 0 0; max-width: 60ch; }}
  .how {{ background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px 18px; font-size: 14px; flex: 0 1 420px; }}
  .how b.t {{ display: block; margin-bottom: 4px; }}
  .how ol {{ margin: 0; padding-left: 18px; }}
  code {{ background: var(--chip); border-radius: 4px; padding: 1px 6px; word-break: break-all; font-size: 13px; }}
  .bar {{ display: flex; align-items: center; gap: 12px; margin: 8px 0 16px; }}
  .bar input {{ flex: 1; max-width: 420px; font: inherit; padding: 9px 12px 9px 36px; border: 1px solid var(--line); border-radius: 8px; background: var(--card) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' fill='none' stroke='%238894a2' stroke-width='2'%3E%3Ccircle cx='7' cy='7' r='5'/%3E%3Cpath d='m11 11 4 4'/%3E%3C/svg%3E") no-repeat 12px center; color: var(--text); }}
  .bar input:focus {{ outline: 2px solid var(--accent); outline-offset: -1px; border-color: transparent; }}
  #count {{ color: var(--muted); font-size: 14px; }}
  .grid {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 14px; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); }}
  .card[hidden] {{ display: none; }}
  .card {{ display: flex; flex-direction: column; background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 16px; }}
  .card .top {{ display: flex; gap: 12px; align-items: center; margin-bottom: 10px; }}
  .card img {{ flex: none; width: 48px; height: 48px; border-radius: 11px; }}
  .card h2 {{ font-size: 16px; margin: 0; line-height: 1.3; }}
  .card .ver {{ display: inline-block; margin-top: 2px; background: var(--chip); color: var(--muted); border-radius: 999px; padding: 0 8px; font-size: 12px; }}
  .card p {{ margin: 0 0 12px; color: var(--muted); font-size: 14px; flex: 1; display: -webkit-box; -webkit-line-clamp: 5; -webkit-box-orient: vertical; overflow: hidden; }}
  .card .links {{ display: flex; gap: 14px; font-size: 14px; }}
  .card .links a {{ text-decoration: none; }} .card .links a:hover {{ text-decoration: underline; }}
  mark {{ background: rgba(255, 196, 0, .35); color: inherit; border-radius: 2px; }}
  .empty {{ color: var(--muted); padding: 32px 0; text-align: center; }}
</style></head><body><main>
<header>
  <div><h1>{e(CONFIG['title'])}</h1><p class="lead">{e(CONFIG['tagline'])}</p></div>
  <div class="how"><b class="t">Add {e(CONFIG['title'])} to Package Center</b><ol>
    <li><b>Package Center</b> → <b>Settings</b> → <b>Package Sources</b> → <b>Add</b></li>
    <li>Name <code>{e(CONFIG['source_name'])}</code>, Location <code id="src">{e(source)}</code></li>
    <li>The apps appear under <b>Community</b>, with their updates.</li></ol></div>
</header>
<div class="bar"><input id="q" type="search" placeholder="Search apps" aria-label="Search apps" autocomplete="off"><span id="count" aria-live="polite"></span></div>
<ul class="grid" id="grid"></ul>
<p class="empty" id="empty" hidden>No app matches that.</p>
<noscript><ul>{plain}</ul></noscript>
</main>
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
