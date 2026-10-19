// Which player_auth_input does the server actually move us for? Phase A: controls only (walk forward, position
// unchanged); phase B: controls plus our own advancing position. The server-side position is read by tools/drive_test.py.
const bedrock = require("bedrock-protocol");
const c = bedrock.createClient({ host: "127.0.0.1", port: 19132, offline: true, username: "Walker", raknetBackend: "raknet-native", useRaknetWorkers: false });
let pos, tick = 0n, yaw = 0, corrections = 0;
const say = (...a) => console.log(new Date().toISOString().slice(14, 19), ...a);
c.on("start_game", (p) => { pos = { ...p.player_position }; tick = BigInt(p.current_tick); say("start", JSON.stringify(pos)); });
c.on("move_player", (p) => { if (p.runtime_id === c.entityId) { pos = { ...p.position }; say("move_player", p.mode, JSON.stringify(p.position)); } });
c.on("correct_player_move_prediction", (p) => { corrections++; pos = { ...p.position }; if (corrections < 6) say("CORRECTION to", JSON.stringify(p.position)); });
c.on("error", (e) => say("error", e.message));
// finishing the spawn: the server says where we really are (state 1); we answer that we're ready (state 2)
c.on("respawn", (p) => {
  say("respawn", p.state, JSON.stringify(p.position));
  if (p.state === 1) {
    pos = { ...p.position };
    c.write("respawn", { position: p.position, state: 2, runtime_entity_id: c.entityId });
    setTimeout(() => {
      c.write("serverbound_loading_screen", { type: 1, loading_screen_id: undefined });
      c.write("serverbound_loading_screen", { type: 2, loading_screen_id: undefined });
      c.write("set_local_player_as_initialized", { runtime_entity_id: c.entityId });
      say("sent loading screen done + initialized");
    }, 500);
  }
});
c.on("spawn", () => {
  say("spawned");
  const t0 = Date.now();
  let last = { ...pos };
  setInterval(() => {
    tick++;
    const s = (Date.now() - t0) / 1000;
    const phase = s < 6 ? "wait" : s < 14 ? "A" : s < 22 ? "B" : "done";
    const walking = phase === "A" || phase === "B";
    if (phase === "B") pos = { ...pos, z: pos.z + 0.21 };  // yaw 0 = facing south (+z)
    const d = { x: pos.x - last.x, y: pos.y - last.y, z: pos.z - last.z };
    last = { ...pos };
    c.write("player_auth_input", {
      pitch: 0, yaw, position: pos, move_vector: { x: 0, z: walking ? 1 : 0 }, head_yaw: yaw,
      input_data: [...(walking ? ["up"] : []), "received_server_data"], input_mode: "touch", play_mode: "normal", interaction_model: "touch",
      interact_rotation: { x: 0, z: yaw }, tick, delta: d,
      analogue_move_vector: { x: 0, z: walking ? 1 : 0 }, camera_orientation: { x: 0, y: 0, z: 1 },
      raw_move_vector: { x: 0, z: walking ? 1 : 0 },
    });
    if (Math.abs(s - 6) < 0.03 || Math.abs(s - 14) < 0.03 || Math.abs(s - 22) < 0.03) say("phase", phase, "my pos", JSON.stringify(pos), "corrections", corrections);
    if (s > 24) process.exit(0);
  }, 50);
});
