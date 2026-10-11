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
    req = urllib.request.Request("https://api.github.com/" + path, headers={"Accept": "application/vnd.github+json", "User-Agent": "dsm-package-source"})
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
        req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "dsm-package-source"})
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
    e = html.escape
    source = (CONFIG.get("base_url") or "").rstrip("/") + "/"   # "/" alone: filled in from the page's own address
    cards = "\n".join(f"""  <li><img src="icons/{p['package']}-256.png" alt="" width="56" height="56">
    <div><h2>{e(p['dname'])} <span>{e(p['version'].rsplit('-', 1)[0])}</span></h2><p>{e(p['desc'])}</p>
    <p class="links"><a href="https://github.com/{e(meta[p['package']]['repo'])}">Source and notes</a> · <a href="{e(p['link'])}">Download .spk</a></p></div></li>""" for p in packages)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(CONFIG['title'])}</title>
<style>
  :root {{ --bg: #f5f7fa; --card: #fff; --text: #1f2933; --muted: #5f6b7a; --line: #dde3ea; --link: #0a66c2; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg: #14181d; --card: #1c2229; --text: #e6eaef; --muted: #9aa5b1; --line: #2c343d; --link: #6cb4ff; }} }}
  body {{ margin: 0; background: var(--bg); color: var(--text); font: 15px/1.5 system-ui, sans-serif; }}
  main {{ max-width: 860px; margin: 0 auto; padding: 32px 16px 48px; }}
  h1 {{ font-size: 26px; margin: 0 0 6px; }} a {{ color: var(--link); }}
  .lead {{ color: var(--muted); margin: 0 0 24px; }}
  .how {{ background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 16px 20px; margin-bottom: 28px; }}
  .how ol {{ margin: 8px 0 0; padding-left: 20px; }} code {{ background: var(--bg); border: 1px solid var(--line); border-radius: 4px; padding: 1px 6px; word-break: break-all; }}
  ul.pk {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 12px; }}
  ul.pk li {{ display: flex; gap: 16px; background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }}
  ul.pk img {{ flex: none; border-radius: 12px; }}
  h2 {{ font-size: 17px; margin: 0 0 4px; }} h2 span {{ color: var(--muted); font-weight: normal; font-size: 14px; }}
  ul.pk p {{ margin: 0 0 6px; color: var(--muted); }} ul.pk p.links {{ margin: 0; font-size: 14px; }}
</style></head><body><main>
<h1>{e(CONFIG['title'])}</h1>
<p class="lead">Self-hosted apps packaged for Synology DSM 7.2, each running in Container Manager with a DSM window of its own.</p>
<div class="how"><b>Add these to Package Center</b><ol>
  <li>Open <b>Package Center</b> → <b>Settings</b> → <b>Package Sources</b> → <b>Add</b>.</li>
  <li>Name: <code>{e(CONFIG['source_name'])}</code> · Location: <code id="src">{e(source)}</code></li>
  <li>The packages appear under <b>Community</b>, and updates show up in Package Center.</li></ol></div>
<ul class="pk">
{cards}
</ul></main>
<script>var s = document.getElementById("src"); if (s.textContent === "/") s.textContent = location.origin + "/";</script>
</body></html>
"""


if __name__ == "__main__":
    sys.exit(main())
