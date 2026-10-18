// Environment audits for the Verity site. Uploaded environments are audited by our audit service
// (dashboard/audit_server.py), which runs each one in an isolated container; this route forwards to it.
// Vercel settings: AUDIT_URL (the service's public address), VERITY_TOKEN (shared with the service).

module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  const base = (process.env.AUDIT_URL || "").replace(/\/$/, "");
  if (!base) return res.status(200).json({ available: false, reason: "no audit service configured" });
  const post = req.method === "POST";
  const id = String(req.query.id || "").replace(/[^a-z0-9-]/gi, "").slice(0, 64);
  try {
    const upstream = await fetch(`${base}/api/audit${id ? `?id=${id}` : ""}`, {
      method: post ? "POST" : "GET",
      headers: { "Content-Type": "application/json", "X-Verity-Token": process.env.VERITY_TOKEN || "" },
      body: post ? JSON.stringify({ filename: String((req.body || {}).filename || "").slice(0, 120), source: String((req.body || {}).source || "").slice(0, 200000) }) : undefined,
      signal: AbortSignal.timeout(15000),
    });
    return res.status(upstream.status).json(await upstream.json());
  } catch (error) {
    return res.status(200).json({ available: false, reason: "audit service unreachable" });
  }
};
