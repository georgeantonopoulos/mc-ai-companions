// Does the server accept a bot walking by sending player_auth_input like a real client?
const bedrock = require("bedrock-protocol");
const c = bedrock.createClient({ host: "127.0.0.1", port: 19132, offline: true, username: "Walker", raknetBackend: "raknet-native", useRaknetWorkers: false });
let pos, tick = 0n, corrections = 0, moves = 0, started = false;
c.on("start_game", (p) => { pos = { ...p.player_position }; tick = BigInt(p.current_tick ?? 0); console.log("start", JSON.stringify(pos)); });
c.on("correct_player_move_prediction", (p) => { corrections++; if (corrections < 5) console.log("CORRECTION", JSON.stringify(p.position)); pos = { ...p.position }; });
c.on("move_player", (p) => { if (p.runtime_id === c.entityId) { moves++; console.log("move_player", p.mode, JSON.stringify(p.position)); pos = { ...p.position }; } });
c.on("spawn", () => {
  console.log("spawned");
  setTimeout(() => { started = true; }, 3000);
  let n = 0;
  const iv = setInterval(() => {
    tick++;
    const walking = started && n < 100;
    const dx = walking ? 0.2 : 0;
    if (walking) n++;
    pos.x += dx;
    c.write("player_auth_input", {
      pitch: 0, yaw: -90, position: pos, move_vector: { x: 0, z: walking ? 1 : 0 }, head_yaw: -90,
      input_data: walking ? ["up"] : [], input_mode: "mouse", play_mode: "normal", interaction_model: "crosshair",
      interact_rotation: { x: 0, z: 0 }, tick, delta: { x: dx, y: 0, z: 0 },
      analogue_move_vector: { x: 0, z: walking ? 1 : 0 }, camera_orientation: { x: 1, y: 0, z: 0 }, raw_move_vector: { x: 0, z: walking ? 1 : 0 },
    });
    if (n === 100 && started) { console.log("done walking; pos", JSON.stringify(pos), "corrections", corrections, "move_player", moves); n++; setTimeout(() => process.exit(0), 3000); }
  }, 50);
});
c.on("error", (e) => console.log("error", e.message));
c.on("kick", (p) => { console.log("kick", JSON.stringify(p)); process.exit(1); });
