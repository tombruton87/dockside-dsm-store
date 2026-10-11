// Answers Package Center at "/" with site/index.json, whether DSM asks by GET or POST
// (a plain static host refuses POST), and leaves out packages that need a newer DSM
// than the one asking. People opening "/" in a browser get site/index.html; the
// icons and everything else are served as static files.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/" && url.pathname !== "/index.json") return env.ASSETS.fetch(request);
    const params = request.method === "POST"
      ? new URLSearchParams(await request.text().catch(() => ""))
      : url.searchParams;
    const fromDsm = request.method === "POST" || url.pathname === "/index.json" || params.has("arch") || params.has("unique");
    if (!fromDsm) return env.ASSETS.fetch(new Request(new URL("/", url)));

    // What DSM sends, without the model or serial: enough to see GET vs POST in the logs.
    // (The page's own fetch of /index.json carries no parameters and isn't logged.)
    if ([...params.keys()].length) console.log(JSON.stringify({ method: request.method, keys: [...params.keys()], build: params.get("build"), major: params.get("major"), minor: params.get("minor"), language: params.get("language") }));

    const asset = path => env.ASSETS.fetch(new Request(new URL(path, url))).then(r => r.json());
    const [catalog, meta] = await Promise.all([asset("/index.json"), asset("/meta.json")]);
    const build = parseInt(params.get("build") || "0", 10);
    const absolute = u => new URL(u, url.origin + "/").href;
    const packages = catalog.packages
      .filter(p => !build || build >= minBuild(meta[p.package]))
      .map(p => ({ ...p, thumbnail: p.thumbnail.map(absolute), thumbnail_retina: (p.thumbnail_retina || []).map(absolute) }));
    return Response.json({ packages }, { headers: { "Cache-Control": "public, max-age=300" } });
  },
};

// "7.2.1-69057" → 69057, DSM's build number; 0 when unknown, so nothing is hidden by mistake.
function minBuild(m) {
  const b = /-(\d+)$/.exec((m && m.os_min_ver) || "");
  return b ? parseInt(b[1], 10) : 0;
}
