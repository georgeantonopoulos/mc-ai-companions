// Joins and prints what the server tells other players about everyone's skin.
const bedrock = require("bedrock-protocol");
const c = bedrock.createClient({ host: "127.0.0.1", port: 19132, offline: true, username: "Watcher", raknetBackend: "raknet-native", useRaknetWorkers: false });
c.on("player_list", (p) => {
  for (const r of (Array.isArray(p.records) ? p.records : p.records?.records) || []) {
    const s = r.skin_data;
    console.log("REC", r.username, r.type, s ? JSON.stringify({ skin_id: s.skin_id, size: [s.skin_data.width, s.skin_data.height], trusted: s.trusted, arm: s.arm_size, persona: s.persona, res: s.skin_resource_pack.slice(0, 90) }) : "-");
  }
});
c.on("error", (e) => console.log("ERR", String(e.message).slice(0, 200)));
c.on("spawn", () => console.log("watcher spawned"));
setTimeout(() => process.exit(0), 30000);
