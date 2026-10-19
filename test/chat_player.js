// A stand-in for a real player (for test/brain_test.py): joins with the name in NAME, finishes loading like a real
// game, then sends each line of SAY as chat, one every GAP seconds (starting after WAIT seconds), and stays.
const bedrock = require("bedrock-protocol");
const NAME = process.env.NAME || "PlayerOne";
const SAY = (process.env.SAY || "").split("|").filter(Boolean);
const client = bedrock.createClient({ host: "127.0.0.1", port: 19132, offline: true, username: NAME,
  raknetBackend: "raknet-native", useRaknetWorkers: false });
let ready = false;
client.on("respawn", (p) => {
  if (ready || p.state !== 1) return;
  ready = true;
  client.write("respawn", { position: p.position, state: 2, runtime_entity_id: client.entityId });
  client.write("serverbound_loading_screen", { type: 1, loading_screen_id: undefined });
  client.write("serverbound_loading_screen", { type: 2, loading_screen_id: undefined });
  client.write("set_local_player_as_initialized", { runtime_entity_id: client.entityId });
  console.log("[player] in the game as", NAME);
  SAY.forEach((line, i) => setTimeout(() => {
    client.write("text", { needs_translation: false, category: "authored", type: "chat", source_name: NAME, message: line,
      xuid: "", platform_chat_id: "", has_filtered_message: false });
    console.log("[player] said:", line);
  }, (Number(process.env.WAIT || 20) + i * Number(process.env.GAP || 30)) * 1000));
});
client.on("text", (p) => { if (p.type === "chat" || p.type === "raw" || p.type === "json") console.log("[player] heard:", p.source_name || "", p.message); });
client.on("kick", (p) => { console.log("[player] kicked", JSON.stringify(p)); process.exit(1); });
client.on("error", (e) => console.log("[player] error", e.message));
