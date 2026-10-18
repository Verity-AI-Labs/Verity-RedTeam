// Live demos for the Verity site. The trained models run on our GPU inference server (dashboard/live.py);
// this route forwards the page's requests to it. Vercel settings:
//   INFERENCE_URL  the inference server's public address
//   VERITY_TOKEN   the token the inference server was started with; needed to start or stop a demo
// With no server configured, or the server unreachable, the page is told live play is offline.

const DEMOS = new Set(["chess", "shop"]);
const ACTIONS = new Set(["start", "stop"]);

module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  const demo = String(req.query.demo || "chess");
  if (!DEMOS.has(demo)) return res.status(400).json({ error: "demo must be chess or shop" });
  const base = (process.env.INFERENCE_URL || "").replace(/\/$/, "");
  if (!base) return res.status(200).json({ available: false, reason: "no inference server configured" });
  const post = req.method === "POST";
  const action = post ? String(req.query.action || "") : "";
  if (post && !ACTIONS.has(action)) return res.status(400).json({ error: "action must be start or stop" });
  try {
    const session = String(req.query.session || "").replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64);  // each visitor's own demo
    const upstream = await fetch(`${base}/api/live?demo=${demo}${post ? `&action=${action}` : ""}&session=${session}`, {
      method: post ? "POST" : "GET",
      headers: { "Content-Type": "application/json", "X-Verity-Token": process.env.VERITY_TOKEN || "" },
      body: post ? JSON.stringify(req.body || {}) : undefined,
      signal: AbortSignal.timeout(8000),
    });
    return res.status(upstream.status).json(await upstream.json());
  } catch (error) {
    return res.status(200).json({ available: false, reason: "inference server unreachable" });
  }
};
