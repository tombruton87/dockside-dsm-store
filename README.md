# Dockside DSM Store

A Package Center source for self-hosted apps packaged for Synology DSM 7.2. Add it once and the
packages appear under **Community** in Package Center, and their updates arrive
there like any other package's.

**Package Center → Settings → Package Sources → Add**
- Name: `Dockside DSM Store`
- Location: *(the address of the deployed site)*

Every package needs Container Manager and DSM 7.2.1 or later.

## How it works

`build.py` reads the latest GitHub release of each repo in `repos.txt`, opens
its versioned `.spk`, and writes a static site:

| File | What it is |
|---|---|
| `site/index.json` | the catalog Package Center reads: `{"packages": [...]}` |
| `site/meta.json` | each package's minimum DSM and the release it came from |
| `site/icons/` | the icons out of each `.spk` |
| `site/index.html` | a page for people: loads `index.json` and lists the apps in a grid, with a search |
| `site/tour.html`, `site/tour.json` | a tour of each app's DSM window, from the screenshots in its README at that release |
| `site/style.css`, `site/art/` | the look: a harbour, made with Higgsfield |

The `.spk` downloads themselves stay on GitHub. Each catalog entry links to
the release asset, with its md5 and size. The pages are built from `templates/` (`@TOKEN@` slots, filled from `config.json`).
`cache.json` remembers what came out of
each release asset so an unchanged package isn't downloaded again. If one
lookup fails, that package keeps its last good entry rather than dropping off
the feed.

The GitHub Action runs `build.py` every hour (and on demand), commits what
changed and deploys. A new release is in Package Center within the hour; run
the workflow by hand to skip the wait.

```bash
GH_TOKEN=$(gh auth token) python3 build.py
```

Adding a package: put its repo in `repos.txt`. Its latest release must carry
exactly one `<pkg>-<version>-<build>.spk`, whose `INFO` agrees with that name.

## Hosting

Everything is static except one thing: Package Center may ask for the catalog
with a POST, which plain static hosts (GitHub Pages, S3) refuse with a 405.

**Cloudflare (now).** `wrangler.jsonc` deploys `site/` as static files with a
small Worker (`worker/index.js`) in front of `/` only. It answers DSM by GET or
POST, leaves out packages that need a newer DSM than the one asking, makes the
icon addresses absolute, and sends browsers to the page. It logs each
request's method and DSM build, never the model or serial, so the logs show
what DSM actually sends. Location: `https://<worker>.workers.dev/`, or a custom
domain.

Deploys need two repository secrets: `CLOUDFLARE_API_TOKEN` (the "Edit
Cloudflare Workers" template) and `CLOUDFLARE_ACCOUNT_ID`.

**A static host (S3, GitHub Pages, …).** Only if the Worker's logs show DSM
asking by GET. Set `base_url` in `config.json` to the site's address (static
files can't make the icon addresses absolute themselves), upload `site/`, and
use `https://<host>/index.json` as the Location. On AWS that's an S3 bucket
behind CloudFront. If DSM turns out to POST, CloudFront needs a small Lambda
in front of the catalog to answer it.
