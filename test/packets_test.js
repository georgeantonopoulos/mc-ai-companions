// What does the server send us in the first seconds after spawning? (names and a few key ones in detail)
const bedrock = require("bedrock-protocol");
const c = bedrock.createClient({ host: "127.0.0.1", port: 19132, offline: true, username: "Walker", raknetBackend: "raknet-native", useRaknetWorkers: false });
const seen = {};
let spawned = 0;
c.on("packet", (pk) => {
  const n = pk.data?.name;
  if (!n) return;
  seen[n] = (seen[n] || 0) + 1;
  if (["move_player", "correct_player_move_prediction", "set_spawn_position", "network_chunk_publisher_update", "set_entity_motion", "play_status", "respawn", "update_attributes"].includes(n) && seen[n] < 3)
    console.log("PKT", n, JSON.stringify(pk.data.params, (k, v) => (typeof v === "bigint" ? String(v) : v)).slice(0, 260));
});
c.on("spawn", () => { spawned = Date.now(); console.log("spawned"); });
setInterval(() => { if (spawned && Date.now() - spawned > 8000) { console.log("ALL", JSON.stringify(seen)); process.exit(0); } }, 500);
