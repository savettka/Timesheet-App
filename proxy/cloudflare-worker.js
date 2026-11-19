// STM on a domain of your own: a Cloudflare Worker that passes every visit on
// to the app where it runs, and its answer back. See "Your own domain,
// through a proxy" in the README.
//
// The Worker's settings (Settings, Variables and Secrets):
//   ORIGIN        where STM runs, such as https://<you>.pythonanywhere.com (text)
//   PROXY_SECRET  the same value as STM_PROXY_SECRET on the server (secret)

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = new URL(env.ORIGIN);
    const target = new URL(url.pathname + url.search, origin);

    // What the Worker saw, which STM believes only with the secret right.
    // Set here, so a visitor's own X-STM-* headers never get through.
    const headers = new Headers(request.headers);
    headers.set("X-STM-Proxy", env.PROXY_SECRET || "");
    headers.set("X-STM-Client-IP", request.headers.get("CF-Connecting-IP") || "");
    headers.set("X-STM-Host", url.host);
    headers.set("X-STM-Proto", url.protocol.replace(":", ""));

    // A form or a photo is read whole before it goes on: STM's largest is
    // 8 MB, and a whole body passes the same way on every runtime.
    const hasBody = request.method !== "GET" && request.method !== "HEAD";
    const answer = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : null,
      redirect: "manual",
    });

    // A redirect to where STM runs comes back as one to your domain.
    const out = new Headers(answer.headers);
    const location = out.get("Location");
    if (location) {
      const to = new URL(location, origin);
      if (to.host === origin.host) {
        to.protocol = url.protocol;
        to.host = url.host;
        out.set("Location", to.toString());
      }
    }
    return new Response(answer.body, { status: answer.status, statusText: answer.statusText, headers: out });
  },
};
