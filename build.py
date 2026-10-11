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
    packages, meta, failed, tour = [], {}, [], []
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
            if got.get("shots_tag") != rel["tag_name"] or any("w" not in x for x in got.get("shots", [])):
                try: got["shots"], got["shots_tag"] = screenshots(repo, rel["tag_name"]), rel["tag_name"]
                except Exception as e: warn(f"{repo}: screenshots — {e}")   # keeps the ones it had
            tour.append({"package": pkg, "dname": entry["dname"], "icon": icon(256), "repo": repo,
                         "link": entry["link"], "shots": got.get("shots", [])})
        except Exception as e:   # a failed lookup keeps the package's last good entry; it never drops off the feed
            prev = [k for k, v in old_meta.items() if v.get("repo") == repo]
            packages += [p for p in old if p["package"] in prev]; meta.update({k: old_meta[k] for k in prev})
            warn(f"{repo}: {e} — " + ("kept the previous entry" if prev else "not listed"))
            failed.append(repo)
    packages.sort(key=lambda p: p["dname"].lower())
    write(os.path.join(SITE, "index.json"), json.dumps({"packages": packages}, indent=1, ensure_ascii=False) + "\n")
    write(os.path.join(SITE, "meta.json"), json.dumps(meta, indent=1, sort_keys=True) + "\n")
    old_tour = load(os.path.join(SITE, "tour.json"), {"apps": []})["apps"]
    have = {t["package"] for t in tour}
    tour += [t for t in old_tour if t["package"] in meta and t["package"] not in have]   # packages kept from before
    tour = sorted((t for t in tour if t["shots"]), key=lambda t: t["dname"].lower())
    default = CONFIG.get("tour_default") if any(t["package"] == CONFIG.get("tour_default") for t in tour) else (tour[0]["package"] if tour else "")
    write(os.path.join(SITE, "tour.json"), json.dumps({"default": default, "apps": tour}, indent=1, ensure_ascii=False) + "\n")
    noscript = "".join(f'<li><a href="{html.escape(p["link"])}">{html.escape(p["dname"])} {html.escape(p["version"].rsplit("-", 1)[0])}</a></li>' for p in packages)
    write(os.path.join(SITE, "index.html"), render("index.html", NOSCRIPT=noscript))
    write(os.path.join(SITE, "tour.html"), render("tour.html"))
    write(os.path.join(SITE, "style.css"), open(os.path.join(ROOT, "templates", "style.css")).read())
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


def render(name, **tokens):
    """A page from templates/: @TOKEN@ slots filled in, every one of them."""
    text = open(os.path.join(ROOT, "templates", name)).read()
    common = {"TITLE": html.escape(CONFIG["title"]), "TAGLINE": html.escape(CONFIG["tagline"]),
              "SOURCE_NAME": html.escape(CONFIG["source_name"]),
              # What to type into Package Center; "/" alone: the page fills in its own address.
              "SOURCE": html.escape((CONFIG.get("base_url") or "").rstrip("/") + "/" + (CONFIG.get("catalog_file") or "")),
              "LOGO": LOGO, "FAVICON": "data:image/svg+xml," + quote(LOGO)}
    for k, v in {**common, **tokens}.items(): text = text.replace(f"@{k}@", v)
    left = re.findall(r"@[A-Z_]+@", text)
    if left: raise ValueError(f"{name}: unfilled {left}")
    return text


def screenshots(repo, tag):
    """The window's screenshots as the package's README shows them at that release: [{title, src, w, h}]."""
    req = urllib.request.Request(f"https://raw.githubusercontent.com/{repo}/{tag}/README.md", headers={"User-Agent": "dockside-dsm-store"})
    with urllib.request.urlopen(req, timeout=30) as r: readme = r.read().decode()
    shots = []
    for alt, path in re.findall(r"!\[([^\]]*)\]\((docs/screenshots/[^)\s]+\.(?:png|jpg|jpeg|webp))\)", readme):
        src = f"https://raw.githubusercontent.com/{repo}/{tag}/{path}"
        shot = {"title": alt.strip() or os.path.basename(path).rsplit(".", 1)[0].title(), "src": src}
        shot.update(png_size(src))   # each screen keeps its own shape on the page
        shots.append(shot)
    return shots


def png_size(url):
    """{w, h} from a PNG's header, fetched as its first 32 bytes; {} for anything else."""
    req = urllib.request.Request(url, headers={"User-Agent": "dockside-dsm-store", "Range": "bytes=0-31"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r: head = r.read(32)
    except OSError: return {}
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR": return {}
    return {"w": int.from_bytes(head[16:20], "big"), "h": int.from_bytes(head[20:24], "big")}

if __name__ == "__main__":
    sys.exit(main())
