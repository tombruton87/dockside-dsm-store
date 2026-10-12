# Dockside DSM Store

A Package Center source for self-hosted apps packaged for Synology DSM 7.2. Add it once and the
packages appear under **Community** in Package Center, and their updates arrive
there like any other package's.

**Package Center → Settings → Package Sources → Add**
- Name: `Dockside DSM Store`
- Location: `https://tombruton87.github.io/dockside-dsm-store/index.json`

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

To see packages that aren't released yet, list them in `pending.txt` and run
`python3 build.py --preview`. It writes `preview/` (never published) with
everything relative, taking those packages from their folders here: the built
`.spk`, icons, screenshots and changelog.

Adding a package: put its repo in `repos.txt`. Its latest release must carry
exactly one `<pkg>-<version>-<build>.spk`, whose `INFO` agrees with that name.

## Hosting

**GitHub Pages (now).** The Action publishes `site/` to
`https://tombruton87.github.io/dockside-dsm-store/`. Package Center reads
`index.json` from there directly. `base_url` in `config.json` makes the icon
addresses absolute, and `catalog_file` is what the page tells people to type.

Pages answers only GET. If Package Center turns out to ask by POST (it then
says the location isn't valid), put the Cloudflare Worker in front instead:

**Cloudflare Worker (ready, not deployed).** `wrangler.jsonc` deploys `site/` as
static files with `worker/index.js` in front of `/`. It answers DSM by GET or
POST, leaves out packages that need a newer DSM than the one asking, and logs
each request's method and DSM build (never the model or serial). Set
`catalog_file` to `""` and `base_url` to the Worker's address, and add the
repository secrets `CLOUDFLARE_API_TOKEN` ("Edit Cloudflare Workers" template)
and `CLOUDFLARE_ACCOUNT_ID`; the Action then deploys there too.
