// A companion's connection to the server: a real player named $NAME wearing the skin in $SKIN. It hears chat
// (printed as "[chat] name: text" for brain.py) and respawns after dying. Moving and acting are done in the
// world by Claude_BP. Runs in an x86 node container sharing the play server's network (see brain.py).
const bedrock = require("bedrock-protocol");
const fs = require("fs");

const NAME = process.env.NAME || "Claude";
const slim = process.env.SLIM === "1";
const b64 = (t) => Buffer.from(t).toString("base64");
// a classic 64x64 skin (skins/paint.py), on the standard wide or slim (thin-armed) player model
const skinData = process.env.SKIN && {
  SkinId: "custom-" + NAME.toLowerCase(),
  SkinData: fs.readFileSync(process.env.SKIN, "utf8").trim(),
  SkinImageWidth: 64,
  SkinImageHeight: 64,
  SkinResourcePatch: b64(JSON.stringify({ geometry: { default: slim ? "geometry.humanoid.customSlim" : "geometry.humanoid.custom" } })),
  SkinGeometryData: "",
  SkinAnimationData: "",
  AnimatedImageData: [],
  PersonaSkin: false,
  PersonaPieces: [],
  PieceTintColors: [],
  ArmSize: slim ? "slim" : "wide",
  PremiumSkin: false,
  CapeData: "",
  CapeId: "",
  CapeImageWidth: 0,
  CapeImageHeight: 0,
  CapeOnClassicSkin: false,
  SkinColor: "#0",
};
// The server files a player without an Xbox account under these ids, which bedrock-protocol makes up fresh on
// every join: so each reconnect was a new player (empty inventory, back at spawn). Keep them fixed per name.
const crypto = require("crypto");
const idFor = (salt) => {
  const h = crypto.createHash("md5").update(salt + ":" + NAME).digest("hex");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20, 32)}`;
};
const identity = { SelfSignedId: idFor("self"), DeviceId: idFor("device"), PlayFabId: idFor("playfab").replace(/-/g, "").slice(0, 16) };

const client = bedrock.createClient({
  host: "127.0.0.1",
  port: 19132,
  offline: true,
  username: NAME,
  skinData: { ...(skinData || {}), ...identity },
  viewDistance: 8,
  raknetBackend: "raknet-native",
  useRaknetWorkers: false,
});

const say = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
let me = { pos: { x: 0, y: 0, z: 0 } };

client.on("start_game", (p) => {
  me.pos = p.player_position;
  say("[body] joined at", JSON.stringify(p.player_position));
});
client.on("spawn", () => say("[body] spawned"));
// Moves come from Claude_BP (server-side teleports). A real client answers every tick with where it is;
// without that the server keeps snapping Claude back to where it last heard from him, which looks like
// flickering to everyone else. So: report the position the server last gave us, every tick.
let tick = 0n;
let last = null;
let teleported = false;
let yaw = 0;
let pitch = 0;
const isMe = (id) => String(id) === String(client.entityId); // the library mixes numbers and BigInts
client.on("move_player", (p) => {
  if (!isMe(p.runtime_id)) return;
  say("[body] moved by the server", p.mode, JSON.stringify(p.position));
  me.pos = p.position;
  yaw = p.yaw;
  pitch = p.pitch;
  teleported = true;
});
let corrections = 0;
client.on("correct_player_move_prediction", (p) => {
  if (++corrections % 100 === 1) say("[body] server corrected my position", corrections, "times so far");
  me.pos = p.position;
  teleported = true;
});
client.on("start_game", (p) => {
  tick = BigInt(p.current_tick ?? 0);
  say("[body] start tick", String(p.current_tick), "movement", JSON.stringify(p.movement_authority ?? p.server_authoritative_movement ?? null),
      "rewind", JSON.stringify(p.rewind_history_size ?? null));
});
client.on("tick_sync", (p) => say("[body] tick_sync", JSON.stringify(p, (k, v) => (typeof v === "bigint" ? String(v) : v))));
client.on("spawn", () => {
  setInterval(() => {
    tick++;
    const pos = me.pos;
    const d = last ? { x: pos.x - last.x, y: pos.y - last.y, z: pos.z - last.z } : { x: 0, y: 0, z: 0 };
    const live = ctrl && Date.now() - ctrlAt < 200 ? ctrl : null;
    const moving = Math.hypot(d.x, d.z) > 0.01 || Math.abs(d.y) > 0.01 || !!(live && live.f);
    // standing still: tell the server only now and then (every half second); moving: every tick, like a client
    if (!moving && !teleported && !ctrl && tick % 10n !== 0n) return;
    if (ctrl) jumping = !!(live && live.j);
    last = { ...pos };
    try {
      client.write("player_auth_input", {
        pitch, yaw, position: pos, move_vector: { x: 0, z: (ctrl ? live && live.f : moving) ? 1 : 0 }, head_yaw: yaw,
        input_data: [...(teleported ? ["handled_teleport"] : []), ...(moving && (!ctrl || (live && live.f)) ? ["up"] : []),
                     ...(jumping ? ["jump_down", "jumping", "start_jumping", "jump_pressed_raw", "jump_current_raw"] : []),
                     ...(live && live.s ? ["sprint_down", "sprinting"] : []), ...(live && live.sn ? ["sneak_down", "sneaking"] : [])],
        input_mode: "touch", play_mode: "normal", interaction_model: "touch",
        interact_rotation: { x: pitch, z: yaw }, tick, delta: d,
        analogue_move_vector: { x: 0, z: (ctrl ? live && live.f : moving) ? 1 : 0 },
        camera_orientation: { x: 0, y: 0, z: 1 }, raw_move_vector: { x: 0, z: (ctrl ? live && live.f : moving) ? 1 : 0 },
      });
    } catch (e) {
      say("[body] input failed", e.message);
    }
    teleported = false;
  }, 50);
});
// Orders from Claude_BP come through the action bar ("@@{json}"): where to stand this tick ("m"), where to look ("r":
// pitch, yaw), jumping ("j"), and arm swings ("s"). We then move like any player's game: report the position each
// tick with walking input, so other players see legs walking and arms swinging.
let jumping = false;
// real controls from Claude_BP ("c"): forward, yaw/pitch, jump, sprint, sneak; they lapse if not renewed
let ctrl = null;
let ctrlAt = 0;
let orders = 0;
setInterval(() => orders && (say("[body] orders in the last 10s:", orders, "corrections so far:", corrections), (orders = 0)), 10000);
client.on("set_title", (p) => {
  if (!String(p.text).startsWith("@@")) return;
  orders++;
  let o;
  try {
    o = JSON.parse(p.text.slice(2));
  } catch {
    return;
  }
  if (o.c) {
    ctrl = o;
    ctrlAt = Date.now();
    yaw = o.y ?? yaw;
    pitch = o.p ?? pitch;
  }
  if (o.m) {
    me.pos = { x: o.m[0], y: o.m[1] + 1.62, z: o.m[2] }; // the protocol's player position is at eye height
    if (o.r) [pitch, yaw] = o.r;
    jumping = !!o.j;
  }
  if (o.s) {
    try {
      client.write("animate", { action_id: "swing_arm", runtime_entity_id: client.entityId, data: 0, has_swing_source: false });
    } catch (e) {
      say("[body] swing failed", e.message);
    }
  }
});
client.on("text", (p) => {
  if (p.type === "chat" && p.source_name && p.source_name !== NAME) say("[chat]", p.source_name + ":", p.message);
  if (p.type === "translation" && /death\./.test(p.message)) say("[event]", p.message, (p.parameters || []).join(","));
});
// after dying: when the server is ready, tell it we want to respawn
// Through a portal: like a real game, take the new position, finish the loading screen and confirm the move,
// otherwise the server keeps us frozen in the new dimension.
client.on("change_dimension", (p) => {
  say("[body] changing dimension to", p.dimension);
  me.pos = p.position;
  teleported = true;
  setTimeout(() => {
    try {
      client.write("serverbound_loading_screen", { type: 1, loading_screen_id: p.loading_screen_id });
      client.write("serverbound_loading_screen", { type: 2, loading_screen_id: p.loading_screen_id });
      client.write("player_action", { runtime_entity_id: client.entityId, action: "dimension_change_ack",
        position: { x: 0, y: 0, z: 0 }, result_position: { x: 0, y: 0, z: 0 }, face: 0 });
      say("[body] arrived in dimension", p.dimension);
    } catch (e) {
      say("[body] dimension change failed", e.message);
    }
  }, 1500);
});

let alive = false;
client.on("respawn", (p) => {
  say("[body] respawn state", p.state);
  // "searching" after we've been playing means we died: press Respawn, like a player on the death screen
  if (p.state === 0 && alive) {
    alive = false;
    setTimeout(() => {
      try {
        // what a current game sends when you tap Respawn: "client ready to spawn", then the server places us
        client.write("respawn", { position: p.position, state: 2, runtime_entity_id: client.entityId });
        client.write("player_action", { runtime_entity_id: client.entityId, action: "respawn",
          position: { x: 0, y: 0, z: 0 }, result_position: { x: 0, y: 0, z: 0 }, face: -1 });
        say("[body] pressed respawn");
      } catch (e) {
        say("[body] respawn press failed", e.message);
      }
    }, 1500);
  }
  if (p.state === 1) alive = true;
  if (p.state === 1) {
    me.pos = p.position; // where the server really put us
    client.write("respawn", { position: p.position, state: 2, runtime_entity_id: client.entityId });
    // like a real game when its loading screen goes away: until then the server keeps a player frozen
    setTimeout(() => {
      try {
        client.write("serverbound_loading_screen", { type: 1, loading_screen_id: undefined });
        client.write("serverbound_loading_screen", { type: 2, loading_screen_id: undefined });
        client.write("set_local_player_as_initialized", { runtime_entity_id: client.entityId });
      } catch (e) {
        say("[body] loading screen", e.message);
      }
    }, 500);
  }
});
client.on("set_health", (p) => {
  if (p.health <= 0) {
    say("[body] died");
    setTimeout(() => {
      try {
        client.write("player_action", {
          runtime_entity_id: client.entityId,
          action: "respawn",
          position: { x: 0, y: 0, z: 0 },
          result_position: { x: 0, y: 0, z: 0 },
          face: -1,
        });
      } catch (e) {
        say("[body] respawn request failed", e.message);
      }
    }, 1500);
  }
});
client.on("disconnect", (p) => {
  say("[body] disconnected:", JSON.stringify(p));
  process.exit(2);
});
client.on("kick", (p) => {
  say("[body] kicked:", JSON.stringify(p));
  process.exit(2);
});
client.on("close", () => {
  say("[body] connection closed");
  process.exit(2);
});
client.on("error", (e) => say("[body] error", e.message));
