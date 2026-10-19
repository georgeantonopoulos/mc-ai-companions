// The body of "Claude", a real player on the server (joined by companion/body.js) whose decisions come
// from Haiku (companion/brain.py). The brain only says WHAT to do; this script does it the way a player
// would: walking step by step, breaking blocks with a tool and waiting, picking up drops, crafting from
// what it carries. Nothing is conjured.
//
// Several companions can play at once (players tagged "companion", one brain each); each has its own state.
//
//   /scriptevent claude:hello <Name> <Buddy>  -> <Name> is a companion who looks after <Buddy>
//   /scriptevent claude:look <Name> <id>      -> prints  [claude] {"who":<Name>,"look":<id>, ...what they see...}
//   /scriptevent claude:do <Name> {"act":..}  -> starts a task (replaces the current one)
import { system, world, ItemStack, EntityDamageCause, BlockPermutation, EnchantmentType } from "@minecraft/server";
import { ACTIONS } from "./actions.js"; // the shared action list (actions.json)

let BOT = ""; // the companion being run right now
const WALK = 0.22; // blocks per tick (a player walks 4.3 b/s)
const RUN = 0.28;
const REACH = 4.5;

let task = null;
let last = "just joined";
let buddy = "";
let keep = 6; // how far from whoever we keep company with before walking over
let anchor = null; // a place to keep to when idle (a build site), instead of following the buddy
let site = null; // the build site's box: the one place where blocks next to players' work may be changed
let tick = 0;
let errAt = -1000;
let goal = null; // where step() was last asked to go
let progress = null; // last place we got clear of, and when
let escape = null; // climbing out of a hole or up a wall
const STUCKSAID = {};

// each companion's own copy of the state above, swapped in while it's being run
const STATES = new Map();
function as(name, fn) {
  const st = STATES.get(name) ?? { task: null, last: "just joined", buddy: "", keep: 6, anchor: null, site: null, goal: null, progress: null, escape: null };
  ({ task, last, buddy, keep, anchor, site, goal, progress, escape } = st);
  BOT = name;
  try {
    return fn();
  } finally {
    STATES.set(name, { task, last, buddy, keep, anchor, site, goal, progress, escape });
  }
}
const companions = () => world.getPlayers({ tags: ["companion"] });

const bot = () => world.getPlayers({ name: BOT })[0];
const out = (o) => console.warn("[claude] " + JSON.stringify({ who: BOT, ...o }));
const r1 = (v) => Math.round(v * 10) / 10;
const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
const flat = (a, b) => Math.hypot(a.x - b.x, a.z - b.z);
const short = (id) => id.replace("minecraft:", "");

// --- the ground --------------------------------------------------------------------------------------

const SOFT = /^minecraft:(air|cave_air|void_air|water|flowing_water|short_grass|tall_grass|short_dry_grass|tall_dry_grass|fern|large_fern|dead_bush|bush|firefly_bush|dandelion|poppy|blue_orchid|allium|azure_bluet|\w+_tulip|oxeye_daisy|cornflower|lily_of_the_valley|wither_rose|sunflower|lilac|rose_bush|peony|pink_petals|wildflowers|leaf_litter|torch|wall_torch|snow_layer|vine|glow_lichen|\w*carpet|\w*button|lever|\w*rail|\w*pressure_plate|\w*sign|\w*mushroom|sugar_cane|\w*sapling|wheat|carrots|potatoes|beetroot|sweet_berry_bush|seagrass|kelp|kelp_plant|cactus_flower)$/;

function blockAt(dim, x, y, z) {
  try {
    return dim.getBlock({ x: Math.floor(x), y: Math.floor(y), z: Math.floor(z) });
  } catch {
    return undefined;
  }
}
const soft = (b) => !!b && (b.isAir || SOFT.test(b.typeId));
const floorOk = (b) => !!b && !b.isAir && !/lava|fire|magma|cactus|sweet_berry|powder_snow/.test(b.typeId) && (!soft(b) || /water/.test(b.typeId));

/** The height a player would stand at in this column, near `fromY`: same level first, then one up, then down. */
function groundY(dim, x, z, fromY, maxUp = 1) {
  const y0 = Math.floor(fromY + 0.01);
  const order = [0];
  for (let u = 1; u <= maxUp; u++) order.push(u);
  order.push(-1, -2, -3);
  for (const dy of order) {
    const y = y0 + dy;
    if (floorOk(blockAt(dim, x, y - 1, z)) && soft(blockAt(dim, x, y, z)) && soft(blockAt(dim, x, y + 1, z))) {
      // must also be able to get there: nothing above our head on the way up
      if (dy > 0 && !soft(blockAt(dim, Math.floor(x), y0 + 2, Math.floor(z)))) continue;
      return y;
    }
  }
  return undefined;
}

// --- moving like a player -----------------------------------------------------------------------------------
// A companion moves itself: the script works out where to step and passes real controls to the companion's own
// connection (body.js, through its action bar), which walks there like any player's game does. Other players see
// real walking (legs, head turning) and real arm swings, and the server does the physics (gravity, water, slopes).
const INTENT = new Map(); // where a companion was last sent with moveTo (for the debug view)
function here(p) {
  return p.location;
}

/** Real controls for a driven companion: which way to face, walk forward or not, sprint, jump, sneak. The server
 *  does the moving (physics, slopes, water, walls), exactly as for a player on an iPad. */
function controls(p, o) {
  p.onScreenDisplay.setActionBar("@@" + JSON.stringify({ c: 1, ...o }));
}
const yawTo = (from, to) => (Math.atan2(-(to.x - from.x), to.z - from.z) * 180) / Math.PI;
const pitchTo = (from, to) => (-Math.atan2(to.y - (from.y + 1.62), Math.hypot(to.x - from.x, to.z - from.z)) * 180) / Math.PI;
function moveTo(p, pos, look, jumping = false) {
  INTENT.set(p.name, { ...pos });
  let r = [0, 0];
  if (look) {
    const dx = look.x - pos.x;
    const dz = look.z - pos.z;
    const dy = look.y - (pos.y + 1.62);
    r = [(-Math.atan2(dy, Math.hypot(dx, dz)) * 180) / Math.PI, (Math.atan2(-dx, dz) * 180) / Math.PI];
  }
  p.onScreenDisplay.setActionBar("@@" + JSON.stringify({ m: [pos.x, pos.y, pos.z], r, j: jumping ? 1 : 0 }));
}
function swing(p) {
  // the real "swing arm" a player's game sends, sent by the companion's connection (body.js) when asked
  p.onScreenDisplay.setActionBar('@@{"s":1}');
}

/** One walking step toward (tx,tz). Returns true if we moved. Steers around trees and walls. */
function step(p, tx, tz, look, speed = WALK) {
  const at = here(p);
  const d = Math.hypot(tx - at.x, tz - at.z);
  // remember where we're trying to get to, so being stuck (no route at all) gets noticed and we dig our way out
  goal = { x: tx, z: tz, y: look?.y ?? navTargetY(p.dimension, tx, tz, look, at.y), tick };
  return drive(p, at, tx, tz, d, look, speed);
}

// --- route-finding (for driven companions) ---------------------------------------------------------------------
// A* over the blocks: a spot is somewhere a player can stand (solid underneath, two blocks of room). From a spot you
// can walk to a neighbour on the same level, jump up one, or drop down up to three. Worked out a few hundred spots per
// tick so the game never stalls; if the target can't be reached, we go to the closest spot we found.
const NAV = new Map(); // name -> { goal, path, i, search, at, stuckAt }
const key3 = (x, y, z) => `${x},${y},${z}`;
const standable = (dim, x, y, z) => floorOk(blockAt(dim, x, y - 1, z)) && soft(blockAt(dim, x, y, z)) && soft(blockAt(dim, x, y + 1, z));

function navTargetY(dim, tx, tz, look, fromY) {
  if (look) return Math.floor(look.y - 1.5);
  const q = world.getPlayers().find((o) => flat(o.location, { x: tx, z: tz }) < 1.5);
  if (q) return Math.floor(q.location.y + 0.01);
  return groundY(dim, tx, tz, fromY, 3) ?? Math.floor(fromY);
}

function startSearch(dim, from, goal) {
  const s = { open: [], came: new Map(), g: new Map(), best: null, bestH: 1e9, n: 0, done: false, goal };
  const k = key3(from.x, from.y, from.z);
  s.g.set(k, 0);
  s.open.push([Math.hypot(goal.x - from.x, goal.y - from.y, goal.z - from.z), from.x, from.y, from.z]);
  return s;
}

function stepSearch(dim, s, budget) {
  const { goal } = s;
  while (budget-- > 0 && s.open.length && !s.done) {
    let bi = 0;
    for (let i = 1; i < s.open.length; i++) if (s.open[i][0] < s.open[bi][0]) bi = i;
    const [, x, y, z] = s.open[bi];
    s.open[bi] = s.open[s.open.length - 1];
    s.open.pop();
    const k = key3(x, y, z);
    const h = Math.hypot(goal.x - x, (goal.y - y) * 1.5, goal.z - z);
    if (h < s.bestH) [s.best, s.bestH] = [[x, y, z], h];
    if (h < 1.5 || ++s.n > 2500) {
      s.done = true;
      break;
    }
    const g0 = s.g.get(k);
    for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]]) {
      const nx = x + dx;
      const nz = z + dz;
      const diag = dx && dz;
      for (const dy of [0, 1, -1, -2, -3]) {
        const ny = y + dy;
        if (!standable(dim, nx, ny, nz)) continue;
        if (dy === 1 && !soft(blockAt(dim, x, y + 2, z))) continue; // no room to jump
        if (dy < 0 && !(soft(blockAt(dim, nx, y, nz)) && soft(blockAt(dim, nx, y + 1, nz)))) continue; // walk off the edge first
        if (diag && (!soft(blockAt(dim, x + dx, Math.max(y, ny), z)) || !soft(blockAt(dim, x, Math.max(y, ny), z + dz)))) continue; // no corner cutting
        const nk = key3(nx, ny, nz);
        const ng = g0 + (diag ? 1.41 : 1) + (dy > 0 ? 0.6 : dy < 0 ? 0.2 * -dy : 0);
        if (ng < (s.g.get(nk) ?? 1e9)) {
          s.g.set(nk, ng);
          s.came.set(nk, k);
          s.open.push([ng + Math.hypot(goal.x - nx, (goal.y - ny) * 1.5, goal.z - nz), nx, ny, nz]);
        }
        break; // one landing per direction: the first that works (same level, then up, then down)
      }
    }
  }
  if (!s.open.length) s.done = true;
  if (!s.done) return null;
  const path = [];
  let k = s.best && key3(...s.best);
  while (k) {
    const [x, y, z] = k.split(",").map(Number);
    path.unshift({ x: x + 0.5, y, z: z + 0.5 });
    k = s.came.get(k);
  }
  return path;
}

/** Walk to (tx, tz) along a planned route with real controls. Returns false when there's no way at all. */
function navigate(p, at, tx, tz, look, speed) {
  const dim = p.dimension;
  const ty = navTargetY(dim, tx, tz, look, at.y);
  const goal = { x: Math.floor(tx), y: ty, z: Math.floor(tz) };
  let n = NAV.get(p.name);
  const moved = n && Math.hypot(n.goal.x - goal.x, n.goal.y - goal.y, n.goal.z - goal.z) > 3;
  const stale = n && tick - n.at > 200;
  const stuck = n && n.path && tick - n.progressAt > 50;
  if (!n || moved || stale || stuck) {
    n = { goal, search: startSearch(dim, { x: Math.floor(at.x), y: Math.floor(at.y + 0.01), z: Math.floor(at.z) }, goal), at: tick, progressAt: tick };
    NAV.set(p.name, n);
  }
  if (n.search) {
    const path = stepSearch(dim, n.search, 350);
    if (!path) return true; // still thinking: keep steering roughly meanwhile
    n.path = path;
    n.i = 1;
    n.search = null;
    n.progressAt = tick;
  }
  if (!n.path || n.path.length < 2 || n.i >= n.path.length) return false;
  const w = n.path[n.i];
  const dx = w.x - at.x;
  const dz = w.z - at.z;
  if (Math.hypot(dx, dz) < 0.45 && Math.abs(w.y - at.y) < 1.2) {
    n.i++;
    n.progressAt = tick;
    if (n.i >= n.path.length) return false;
  }
  const next = n.path[Math.min(n.i, n.path.length - 1)];
  const feet = blockAt(dim, at.x, at.y, at.z);
  controls(p, {
    f: 1,
    y: yawTo(at, next),
    p: look ? pitchTo(at, look) : 0,
    j: next.y > at.y + 0.4 || (!!feet && /water/.test(feet.typeId)) ? 1 : 0,
    s: speed >= RUN && n.path.length - n.i > 6 ? 1 : 0,
  });
  return true;
}

/** One tick of steering: face the first open way toward the target (around walls and trees), press forward, and
 *  jump when there's a step up or we're in water. Close enough: stop. */
function drive(p, at, tx, tz, d, look, speed) {
  // far or blocked: plan a real route; close and clear: just steer
  if (d > 3 && navigate(p, at, tx, tz, look, speed)) return true;
  if (d < 0.35) {
    controls(p, { f: 0, y: yawTo(at, { x: tx, z: tz }), p: look ? pitchTo(at, look) : 0 });
    return true;
  }
  const base = Math.atan2(tz - at.z, tx - at.x);
  const side = task?.side ?? 1;
  const climb = 1;
  for (const off of [0, 0.5, -0.5, 1.0, -1.0, 1.6, -1.6, 2.2, -2.2]) {
    const a = base + off * side;
    const ahead = { x: at.x + Math.cos(a) * 0.9, z: at.z + Math.sin(a) * 0.9 };
    const gy = groundY(p.dimension, ahead.x, ahead.z, at.y, climb);
    if (gy === undefined) continue;
    const feet = blockAt(p.dimension, at.x, at.y, at.z);
    const swimming = !!feet && /water/.test(feet.typeId);
    const target = { x: at.x + Math.cos(a), z: at.z + Math.sin(a) };
    controls(p, {
      f: 1,
      y: yawTo(at, target),
      p: look ? pitchTo(at, look) : 0,
      j: gy > at.y + 0.4 || swimming ? 1 : 0,
      s: speed >= RUN && d > 6 ? 1 : 0,
    });
    if (task) {
      task.stuck = off === 0 ? 0 : (task.stuck ?? 0) + (Math.abs(off) > 1.5 ? 1 : 0);
      if (task.stuck > 60) task.side = -side;
    }
    return true;
  }
  controls(p, { f: 0, y: yawTo(at, { x: tx, z: tz }) });
  if (task) task.stuck = (task.stuck ?? 0) + 1;
  return false;
}

// --- inventory ---------------------------------------------------------------------------------------

const inv = (p) => p.getComponent("minecraft:inventory").container;
const GROUPS = {
  log: /_log$|_stem$/,
  planks: /_planks$/,
  cobble: /^minecraft:(cobblestone|cobbled_deepslate|blackstone)$/,
  coal: /^minecraft:(coal|charcoal)$/,
  sword: /_sword$/,
};
const matcher = (name) => GROUPS[name] ?? new RegExp("^minecraft:" + name.replace("minecraft:", "") + "$");

function count(p, name) {
  const c = inv(p);
  const m = matcher(name);
  let n = 0;
  for (let i = 0; i < c.size; i++) {
    const it = c.getItem(i);
    if (it && m.test(it.typeId)) n += it.amount;
  }
  return n;
}

/** Remove n items matching `name`; returns the typeId of the first one taken. */
function take(p, name, n) {
  const c = inv(p);
  const m = matcher(name);
  let first;
  for (let i = 0; i < c.size && n > 0; i++) {
    const it = c.getItem(i);
    if (!it || !m.test(it.typeId)) continue;
    first ??= it.typeId;
    const k = Math.min(n, it.amount);
    n -= k;
    if (k === it.amount) c.setItem(i, undefined);
    else {
      it.amount -= k;
      c.setItem(i, it);
    }
  }
  return first;
}

function add(p, id, n) {
  while (n > 0) {
    const k = Math.min(n, 64);
    const left = inv(p).addItem(new ItemStack(id, k));
    if (left) p.dimension.spawnItem(left, p.location);
    n -= k;
  }
}

function inventory(p) {
  const c = inv(p);
  const o = {};
  for (let i = 0; i < c.size; i++) {
    const it = c.getItem(i);
    if (it) o[short(it.typeId)] = (o[short(it.typeId)] ?? 0) + it.amount;
  }
  return o;
}

const TIERS = ["netherite", "diamond", "iron", "stone", "golden", "wooden"];
/** Hold the best tool of a kind ("sword", "pickaxe", "axe", "shovel"); returns its tier or undefined. */
function hold(p, kind) {
  const c = inv(p);
  let best = -1;
  let bestTier = 99;
  for (let i = 0; i < c.size; i++) {
    const it = c.getItem(i);
    if (!it || !it.typeId.endsWith("_" + kind)) continue;
    const t = TIERS.findIndex((x) => it.typeId.includes(x + "_"));
    if (t >= 0 && t < bestTier) [best, bestTier] = [i, t];
  }
  if (best < 0) return undefined;
  if (best > 8) {
    c.swapItems(best, p.selectedSlotIndex, c);
  } else p.selectedSlotIndex = best;
  return TIERS[bestTier];
}

function weaponDamage(p) {
  const it = inv(p).getItem(p.selectedSlotIndex)?.typeId ?? "";
  if (p.hasTag("armoured") && it === "minecraft:netherite_sword") return 10000; // "infinite sharpness": one hit, any monster
  const mult = p.hasTag("strong") ? 4 : 1; // a "strong" companion hits like four players (skill, not items)
  const tier = { wooden: 0, golden: 0, stone: 1, iron: 2, diamond: 3, netherite: 4 }[TIERS.find((t) => it.includes(t + "_"))];
  if (it.endsWith("_sword")) return (4 + (tier ?? 0)) * mult;
  if (it.endsWith("_axe")) return (3 + (tier ?? 0)) * mult;
  return 2 * mult;
}

// --- what can be mined, crafted, eaten ----------------------------------------------------------------

const MINE = {
  log: { m: /_log$|_stem$/, tool: "axe", hand: 60, need: -1 },
  wood: { m: /_log$|_stem$/, tool: "axe", hand: 60, need: -1 },
  leaves: { m: /_leaves$/, tool: "", hand: 6, need: -1 },
  dirt: { m: /^minecraft:(dirt|grass_block|coarse_dirt|rooted_dirt|podzol|mud)$/, tool: "shovel", hand: 15, need: -1 },
  sand: { m: /^minecraft:(sand|red_sand)$/, tool: "shovel", hand: 15, need: -1 },
  gravel: { m: /^minecraft:gravel$/, tool: "shovel", hand: 18, need: -1 },
  stone: { m: /^minecraft:(stone|deepslate|andesite|diorite|granite|tuff)$/, tool: "pickaxe", hand: 150, need: 5 },
  coal_ore: { m: /coal_ore$/, tool: "pickaxe", hand: 300, need: 5 },
  copper_ore: { m: /copper_ore$/, tool: "pickaxe", hand: 300, need: 3 },
  iron_ore: { m: /iron_ore$/, tool: "pickaxe", hand: 300, need: 3 },
  gold_ore: { m: /gold_ore$/, tool: "pickaxe", hand: 300, need: 2 },
  diamond_ore: { m: /diamond_ore$/, tool: "pickaxe", hand: 300, need: 2 },
  wool: { m: /_wool$/, tool: "", hand: 24, need: -1 },
  lava: { m: /^minecraft:lava$/, tool: "", hand: 0, need: -1 },
  obsidian: { m: /^minecraft:obsidian$/, tool: "pickaxe", hand: 1500, need: 1 }, // diamond pickaxe, ~9 s
  water: { m: /^minecraft:water$/, tool: "", hand: 0, need: -1 },
};
// a tool makes breaking this many times faster, by tier
const SPEEDUP = { wooden: 2, golden: 6, stone: 4, iron: 6, diamond: 8, netherite: 9 };

const R = {
  planks: { needs: [["log", 1]], out: (from) => [from.replace(/_log$|_stem$/, "_planks"), 4] },
  stick: { needs: [["planks", 2]], out: ["minecraft:stick", 4] },
  crafting_table: { needs: [["planks", 4]], out: ["minecraft:crafting_table", 1] },
  wooden_pickaxe: { needs: [["planks", 3], ["stick", 2]], table: true },
  wooden_axe: { needs: [["planks", 3], ["stick", 2]], table: true },
  wooden_sword: { needs: [["planks", 2], ["stick", 1]], table: true },
  wooden_shovel: { needs: [["planks", 1], ["stick", 2]], table: true },
  stone_pickaxe: { needs: [["cobble", 3], ["stick", 2]], table: true },
  stone_axe: { needs: [["cobble", 3], ["stick", 2]], table: true },
  stone_sword: { needs: [["cobble", 2], ["stick", 1]], table: true },
  stone_shovel: { needs: [["cobble", 1], ["stick", 2]], table: true },
  torch: { needs: [["coal", 1], ["stick", 1]], out: ["minecraft:torch", 4] },
  furnace: { needs: [["cobble", 8]], table: true },
  chest: { needs: [["planks", 8]], table: true },
  bread: { needs: [["wheat", 3]], table: true },
  bowl: { needs: [["planks", 3]], table: true, out: ["minecraft:bowl", 4] },
  ladder: { needs: [["stick", 7]], table: true, out: ["minecraft:ladder", 3] },
  oak_door: { needs: [["planks", 6]], table: true, out: ["minecraft:wooden_door", 3] },
  wooden_door: { needs: [["planks", 6]], table: true, out: ["minecraft:wooden_door", 3] },
  birch_door: { needs: [["birch_planks", 6]], table: true, out: ["minecraft:birch_door", 3] },
  bucket: { needs: [["iron_ingot", 3]], table: true, out: ["minecraft:bucket", 1] },
  iron_pickaxe: { needs: [["iron_ingot", 3], ["stick", 2]], table: true },
  iron_sword: { needs: [["iron_ingot", 2], ["stick", 1]], table: true },
  flint_and_steel: { needs: [["iron_ingot", 1], ["flint", 1]], out: ["minecraft:flint_and_steel", 1] },
  shield: { needs: [["planks", 6], ["iron_ingot", 1]], table: true },
  iron_axe: { needs: [["iron_ingot", 3], ["stick", 2]], table: true },
  diamond_pickaxe: { needs: [["diamond", 3], ["stick", 2]], table: true },
  diamond_sword: { needs: [["diamond", 2], ["stick", 1]], table: true },
  diamond_axe: { needs: [["diamond", 3], ["stick", 2]], table: true },
  bow: { needs: [["stick", 3], ["string", 3]], table: true },
  arrow: { needs: [["flint", 1], ["stick", 1], ["feather", 1]], table: true, out: ["minecraft:arrow", 4] },
  blaze_powder: { needs: [["blaze_rod", 1]], out: ["minecraft:blaze_powder", 2] },
  ender_eye: { needs: [["blaze_powder", 1], ["ender_pearl", 1]], out: ["minecraft:ender_eye", 1] },
  spruce_door: { needs: [["spruce_planks", 6]], table: true, out: ["minecraft:spruce_door", 3] },
  dark_oak_door: { needs: [["dark_oak_planks", 6]], table: true, out: ["minecraft:dark_oak_door", 3] },
  bed: { needs: [["wool", 3], ["planks", 3]], table: true, out: ["minecraft:bed", 1] },
};
GROUPS.wool = /_wool$/;

const FOOD = {
  cooked_beef: 8, cooked_porkchop: 8, cooked_mutton: 6, cooked_chicken: 6, cooked_salmon: 6, cooked_cod: 5,
  bread: 5, baked_potato: 5, apple: 4, carrot: 3, beef: 3, porkchop: 3, mutton: 2, chicken: 2, sweet_berries: 2,
  melon_slice: 2, cookie: 2, potato: 1, golden_apple: 8, cod: 2, salmon: 2, rotten_flesh: 2, egg: 0,
};
const SMELT = {
  beef: "cooked_beef", porkchop: "cooked_porkchop", mutton: "cooked_mutton", chicken: "cooked_chicken",
  cod: "cooked_cod", salmon: "cooked_salmon", potato: "baked_potato", raw_iron: "iron_ingot", raw_gold: "gold_ingot",
  raw_copper: "copper_ingot", sand: "glass", cobblestone: "stone",
};
const ANIMALS = ["cow", "pig", "sheep", "chicken", "rabbit", "mooshroom"];

function nearBlock(p, m, r = 6) {
  const at = p.location;
  for (let x = -r; x <= r; x++)
    for (let z = -r; z <= r; z++)
      for (let y = -3; y <= 3; y++) {
        const b = blockAt(p.dimension, at.x + x, at.y + y, at.z + z);
        if (b && m.test(b.typeId)) return b;
      }
  return undefined;
}

function craft(p, item, n) {
  const key = item.replace("minecraft:", "").replace(/^\w+_planks$/, "planks");
  const rec = R[key];
  if (!rec) return `I don't know how to make ${item}`;
  if (rec.table && count(p, "crafting_table") === 0 && !nearBlock(p, /crafting_table$/))
    return `I need a crafting table for ${item}`;
  let made = 0;
  for (let k = 0; k < n; k++) {
    if (!rec.needs.every(([g, c]) => count(p, g) >= c)) break;
    let from = "";
    for (const [g, c] of rec.needs) from = take(p, g, c) ?? from;
    const [id, q] = typeof rec.out === "function" ? rec.out(from) : (rec.out ?? ["minecraft:" + key, 1]);
    add(p, id, q);
    made += q;
  }
  if (!made) return `not enough to make ${item} (need ${rec.needs.map(([g, c]) => c + " " + g).join(" + ")})`;
  return `made ${made} ${item}`;
}

// --- seeing ------------------------------------------------------------------------------------------

const others = () => world.getPlayers({ excludeTags: ["companion"] });
const hp = (e) => Math.round(e.getComponent("minecraft:health")?.currentValue ?? 0);
const hostiles = (p, at, r) =>
  p.dimension.getEntities({ location: at, maxDistance: r, families: ["monster"] }).filter((e) => e.typeId !== "minecraft:player");

// --- a map of useful things around each companion ---------------------------------------------------------
// A slow sweep (a few hundred blocks a tick, every other column) of a big box around the companion: the nearest few
// spots of each useful material, deep ones included, so it knows where to go for lava, iron, coal...
const SURVEY_KINDS = {
  lava: /^minecraft:lava$/, water: /^minecraft:water$/, iron_ore: /iron_ore$/, coal_ore: /coal_ore$/,
  copper_ore: /copper_ore$/, gold_ore: /gold_ore$/, diamond_ore: /diamond_ore$/, redstone_ore: /redstone_ore$/,
  obsidian: /^minecraft:obsidian$/, log: /_log$/, sand: /^minecraft:sand$/, gravel: /^minecraft:gravel$/,
};
const SURVEYS = new Map(); // name -> { origin, x, z, found: {kind: [[x,y,z],...]}, done }
const SR = 40;
function survey(p, budget = 500) {
  let sv = SURVEYS.get(p.name);
  if (!sv || (sv.done && (tick - sv.doneAt > 2400 || flat(sv.origin, p.location) > 24))) {
    const keep = sv?.found ?? {};
    sv = { origin: { x: Math.floor(p.location.x), y: Math.floor(p.location.y), z: Math.floor(p.location.z) }, x: -SR, z: -SR, found: {}, keep, done: false };
    SURVEYS.set(p.name, sv);
  }
  if (sv.done) return;
  const o = sv.origin;
  while (budget > 0 && !sv.done) {
    for (let y = 15; y >= -30; y -= 1) {
      const b = blockAt(p.dimension, o.x + sv.x, o.y + y, o.z + sv.z);
      budget--;
      if (!b || b.isAir) continue;
      for (const [k, m] of Object.entries(SURVEY_KINDS)) {
        if (!m.test(b.typeId)) continue;
        // keep the 4 nearest to where the sweep started
        const list = (sv.found[k] ??= []);
        const c = [b.location.x, b.location.y, b.location.z];
        const d2 = (q) => (q[0] - o.x) ** 2 + (q[1] - o.y) ** 2 + (q[2] - o.z) ** 2;
        if (list.length < 4 || d2(c) < d2(list[list.length - 1])) {
          list.push(c);
          list.sort((a, b) => d2(a) - d2(b));
          if (list.length > 4) list.pop();
        }
      }
    }
    sv.z += 2;
    if (sv.z > SR) {
      sv.z = -SR;
      sv.x += 2;
      if (sv.x > SR) {
        sv.done = true;
        sv.doneAt = tick;
      }
    }
  }
}
function knownPlaces(p) {
  const sv = SURVEYS.get(p.name);
  if (!sv) return undefined;
  const found = sv.done ? sv.found : { ...sv.keep, ...sv.found };
  const out = {};
  for (const [k, list] of Object.entries(found)) {
    out[k] = list
      .map((c) => ({ at: c, dist: Math.round(Math.hypot(c[0] - p.location.x, c[1] - p.location.y, c[2] - p.location.z)) }))
      .sort((a, b) => a.dist - b.dist)
      .slice(0, 2);
  }
  const animals = {};
  for (const e of p.dimension.getEntities({ location: p.location, maxDistance: 48 })) {
    const t = short(e.typeId);
    if (!ANIMALS.includes(t)) continue;
    const d = Math.round(dist(e.location, p.location));
    if (!animals[t] || d < animals[t].dist) animals[t] = { at: [Math.floor(e.location.x), Math.floor(e.location.y), Math.floor(e.location.z)], dist: d };
  }
  out.animals = animals;
  return out;
}

// the surroundings change slowly: survey them at most once a minute per companion (it reads thousands of blocks)
const SCANS = new Map();
function scanCached(p) {
  const c = SCANS.get(p.name);
  if (c && tick - c.tick < 1200 && flat(c.at, p.location) < 6) return c.found;
  const found = scan(p);
  SCANS.set(p.name, { tick, at: { ...p.location }, found });
  return found;
}

/** What's built inside a box (the build site): far fewer blocks to read than a wide survey. */
function builtIn(dim, box) {
  const o = {};
  for (let y = box[1] - 1; y <= box[4] + 1; y++)
    for (let x = box[0] - 1; x <= box[3] + 1; x++)
      for (let z = box[2] - 1; z <= box[5] + 1; z++) {
        const b = blockAt(dim, x, y, z);
        if (b && !b.isAir && BUILT.test(b.typeId)) (o[short(b.typeId)] ??= []).push([x, y, z]);
      }
  return o;
}

function scan(p, rad = 10) {
  const at = p.location;
  const found = {};
  const kinds = ["log", "stone", "coal_ore", "iron_ore", "copper_ore", "sand", "gravel", "lava", "water"];
  for (let x = -rad; x <= rad; x++)
    for (let z = -rad; z <= rad; z++)
      for (let y = -4; y <= 8; y++) {
        const b = blockAt(p.dimension, at.x + x, at.y + y, at.z + z);
        if (!b || b.isAir) continue;
        for (const k of kinds) {
          if (!MINE[k].m.test(b.typeId)) continue;
          const d = Math.round(Math.hypot(x, y, z));
          const f = (found[k] ??= { n: 0, nearest: d });
          f.n++;
          f.nearest = Math.min(f.nearest, d);
        }
        if (/crafting_table|furnace|chest|bed$/.test(b.typeId)) found[short(b.typeId)] ??= { n: 1, nearest: Math.round(Math.hypot(x, y, z)) };
      }
  return found;
}

/** The blocks a build/dig is about: a list ("blocks"), or a shape a builder thinks in, from one corner to the
 *  other: "fill" (solid, e.g. a floor or roof), "walls" (the ring around the edge, every layer), "line".
 *  "gaps" are left out (doors, windows). Bottom layer first, so everything has something to stand on. */
function cells(t) {
  const ok = (b) => Array.isArray(b) && b.length === 3 && b.every(Number.isFinite);
  const gaps = new Set((Array.isArray(t.gaps) ? t.gaps : []).filter(ok).map((g) => g.map(Math.floor).join(",")));
  let out = [];
  if (Array.isArray(t.blocks)) out = t.blocks.filter(ok).map((b) => b.map(Math.floor));
  else if (ok(t.from) && ok(t.to)) {
    const [a, b] = [t.from.map(Math.floor), t.to.map(Math.floor)];
    const lo = a.map((v, i) => Math.min(v, b[i]));
    const hi = a.map((v, i) => Math.max(v, b[i]));
    if (t.shape === "line") {
      const n = Math.max(...hi.map((h, i) => h - lo[i]));
      for (let k = 0; k <= n; k++) out.push(a.map((v, i) => Math.round(v + ((b[i] - v) * k) / (n || 1))));
    } else
      for (let y = lo[1]; y <= hi[1]; y++)
        for (let x = lo[0]; x <= hi[0]; x++)
          for (let z = lo[2]; z <= hi[2]; z++) {
            const edge = x === lo[0] || x === hi[0] || z === lo[2] || z === hi[2];
            if (t.shape === "walls" && !edge) continue;
            out.push([x, y, z]);
          }
  }
  return out.filter((c) => !gaps.has(c.join(","))).sort((p, q) => p[1] - q[1]);
}

// blocks people put there (not nature's), so a builder can see what's been built and check its work
const BUILT = /_planks$|cobblestone$|glass|_door$|torch$|crafting_table$|furnace$|chest$|_slab$|_stairs$|fence|bricks$|concrete$|_wool$|ladder$|bed$|terracotta$|quartz|lantern$|trapdoor$|carpet$|smooth_stone$|polished_|_pane$|stripped_/;
function builtNear(p, r = 10, cap = 220) {
  const at = p.location;
  const o = {};
  let n = 0;
  for (let y = -2; y <= 8; y++)
    for (let x = -r; x <= r; x++)
      for (let z = -r; z <= r; z++) {
        const b = blockAt(p.dimension, at.x + x, at.y + y, at.z + z);
        if (!b || b.isAir || !BUILT.test(b.typeId)) continue;
        if (++n > cap) return { ...o, more: "too many to list" };
        (o[short(b.typeId)] ??= []).push([b.location.x, b.location.y, b.location.z]);
      }
  return o;
}

function look(id) {
  const p = bot();
  if (!p) return out({ look: id, online: false });
  const ps = others().map((q) => ({
    name: q.name,
    dist: Math.round(dist(q.location, p.location)),
    hp: hp(q),
    at: [Math.floor(q.location.x), Math.floor(q.location.y + 0.01), Math.floor(q.location.z)],
    holding: short(inv(q).getItem(q.selectedSlotIndex)?.typeId ?? "nothing"),
  }));
  const danger = [];
  for (const who of [p, ...others()]) {
    for (const e of hostiles(p, who.location, 16)) {
      danger.push({ mob: short(e.typeId), near: who === p ? "me" : who.name, dist: Math.round(dist(e.location, who.location)) });
    }
  }
  const animals = p.dimension
    .getEntities({ location: p.location, maxDistance: 20 })
    .filter((e) => ANIMALS.includes(short(e.typeId)))
    .map((e) => short(e.typeId) + "@" + Math.round(dist(e.location, p.location)));
  const tod = world.getTimeOfDay();
  out({
    look: id,
    online: true,
    difficulty: String(world.getDifficulty()).toLowerCase(),
    time: tod < 12000 ? "day" : tod < 13000 ? "sunset" : tod < 23000 ? "night" : "dawn",
    me: {
      hp: hp(p),
      at: [Math.floor(p.location.x), Math.floor(p.location.y + 0.01), Math.floor(p.location.z)],
      holding: short(inv(p).getItem(p.selectedSlotIndex)?.typeId ?? "nothing"),
      inventory: inventory(p),
      doing: task ? describe(task) : "nothing",
      last,
    },
    players: ps,
    // the other companions: where they are and what they're up to
    friends: companions().filter((q) => q.name !== p.name).map((q) => ({
      name: q.name,
      dist: Math.round(dist(q.location, p.location)),
      at: [Math.floor(q.location.x), Math.floor(q.location.y + 0.01), Math.floor(q.location.z)],
    })),
    danger: danger.slice(0, 8),
    animals: animals.slice(0, 8),
    nearby: scanCached(p),
    known_places: knownPlaces(p),
    built: site ? builtIn(p.dimension, site) : builtNear(p, 6),
  });
}

function describe(t) {
  const { act, player, block, item, mob, n } = t;
  return [act, player, block, item, mob, n].filter((x) => x !== undefined).join(" ");
}

// --- doing -------------------------------------------------------------------------------------------

function findPlayer(name) {
  const ps = others();
  const is = (q, n) => q.name.toLowerCase() === String(n ?? "").toLowerCase();
  // anyone by name, companions included (a baby following Gaston); else the buddy, else whoever's here
  return world.getPlayers().find((q) => q.name !== BOT && is(q, name)) ?? ps.find((q) => is(q, buddy)) ?? ps[0];
}

function start(t) {
  t.until = tick + (t.secs ?? 60) * 20;
  t.n ??= 1;
  t.done = 0;
  task = t;
}

function finish(msg) {
  last = msg;
  out({ result: msg });
  const prev = task?.prev;
  task = prev && tick < prev.until ? prev : null;
}

const isOpen = (dim, l) =>
  [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]].some(([a, c, e]) => soft(blockAt(dim, l.x + a, l.y + c, l.z + e)));

/** The nearest matching block; ones out in the open first, buried ones (we'll dig to them) count as further. */
/** The nearest matching block in a big box below and around us (sparse rings outward, nearest first). */
function findDeep(p, m, rad, ylo, yhi) {
  const at = p.location;
  for (let r = 0; r <= rad; r++)
    for (let x = -r; x <= r; x++)
      for (let z = -r; z <= r; z++) {
        if (Math.max(Math.abs(x), Math.abs(z)) !== r) continue;
        for (let y = yhi; y >= ylo; y--) {
          const b = blockAt(p.dimension, at.x + x, at.y + y, at.z + z);
          if (b && m.test(b.typeId)) return b;
        }
      }
  return undefined;
}

function findBlock(p, m, rad = 16, bad = new Set()) {
  const found = [];
  const at = p.location;
  for (let x = -rad; x <= rad; x++)
    for (let z = -rad; z <= rad; z++)
      for (let y = -4; y <= 6; y++) {
        const b = blockAt(p.dimension, at.x + x, at.y + y, at.z + z);
        if (!b || !m.test(b.typeId) || bad.has(`${b.location.x},${b.location.y},${b.location.z}`)) continue;
        // high up (tree tops) counts as far: a player can only reach so high
        found.push([Math.hypot(x, y * 1.5, z) + (isOpen(p.dimension, b.location) ? 0 : 8) + (y > 3 ? 12 : 0), b]);
      }
  found.sort((a, b) => a[0] - b[0]);
  // the nearest that isn't part of (or right next to) someone's build
  return found.slice(0, 60).find(([, b]) => !protectedAt(p.dimension, b.location.x, b.location.y, b.location.z))?.[1];
}

// --- other players' work is never broken ---------------------------------------------------------------
// Blocks real players put down are remembered (in the world, so it survives restarts). Companions never break a
// block within 2 of one, or within 2 of anything built (planks, glass, doors...), except at their own build site.
const PLACED = new Set();
const PLACED_KEYS = 8;
let placedDirty = false;
function loadPlaced() {
  for (let k = 0; k < PLACED_KEYS; k++) {
    const v = world.getDynamicProperty("claude:placed" + k);
    if (typeof v === "string" && v) for (const key of v.split(";")) PLACED.add(key);
  }
}
function savePlaced() {
  const all = [...PLACED];
  const per = Math.ceil(all.length / PLACED_KEYS) || 1;
  for (let k = 0; k < PLACED_KEYS; k++) world.setDynamicProperty("claude:placed" + k, all.slice(k * per, (k + 1) * per).join(";"));
  placedDirty = false;
}
world.afterEvents.playerPlaceBlock.subscribe((ev) => {
  if (ev.player.hasTag("companion")) return;
  const l = ev.block.location;
  PLACED.add(`${l.x},${l.y},${l.z}`);
  placedDirty = true;
});
const inSite = (x, y, z) => site && x >= site[0] - 1 && x <= site[3] + 1 && y >= site[1] - 1 && y <= site[4] + 1 && z >= site[2] - 1 && z <= site[5] + 1;
// blocks companions put down themselves (shelters, pillars, jobs): theirs to take away again
const OWNED = new Set();
function protectedAt(dim, x, y, z) {
  if (OWNED.has(`${x},${y},${z}`)) return false;
  // a block a player put down is never ours to break, wherever it is
  if (PLACED.has(`${x},${y},${z}`)) return true;
  // at our own build site: natural ground can go (to lay a floor), but nothing built and nothing a player placed
  if (inSite(x, y, z)) {
    const b = blockAt(dim, x, y, z);
    return !!b && BUILT.test(b.typeId);
  }
  // anywhere else: keep well clear of players' builds (6 blocks round, 4 up and down) and of anything built
  for (let dx = -6; dx <= 6; dx++)
    for (let dz = -6; dz <= 6; dz++)
      for (let dy = -4; dy <= 4; dy++) if (PLACED.has(`${x + dx},${y + dy},${z + dz}`)) return true;
  for (let dx = -2; dx <= 2; dx++)
    for (let dy = -2; dy <= 2; dy++)
      for (let dz = -2; dz <= 2; dz++) {
        const b = blockAt(dim, x + dx, y + dy, z + dz);
        if (b && !b.isAir && BUILT.test(b.typeId)) return true;
      }
  return false;
}
/** Somewhere a player has built (within r): no clutter put down there. */
const nearPlayerBuild = (x, y, z, r = 4) => {
  for (let dx = -r; dx <= r; dx++)
    for (let dz = -r; dz <= r; dz++)
      for (let dy = -3; dy <= 3; dy++) if (PLACED.has(`${x + dx},${y + dy},${z + dz}`)) return true;
  return false;
};
/** Break a block like a player (it drops), unless it belongs to someone's build. Returns whether it broke. */
function breakAt(dim, x, y, z) {
  if (protectedAt(dim, x, y, z)) return false;
  dim.runCommand(`setblock ${x} ${y} ${z} air destroy`);
  lootHere(BOT, { x: x + 0.5, y, z: z + 0.5 });
  return true;
}
// where each companion's own drops are (blocks it broke, things it killed): the only items it may pick up
const LOOT = new Map();
function lootHere(name, at) {
  const l = LOOT.get(name) ?? [];
  l.push({ at, tick });
  LOOT.set(name, l.filter((x) => tick - x.tick < 300).slice(-20));
}
const OWN_DEATH = new Map();
const mine_ = (name, l) =>
  (LOOT.get(name) ?? []).some((x) => tick - x.tick < 300 && dist(x.at, l) < 4) ||
  ((d) => !!d && tick - d.tick < 6000 && dist(d.at, l) < 6)(OWN_DEATH.get(name));

const RUNNERS = {
  follow(p, t) {
    const q = findPlayer(t.player);
    if (!q) return finish("nobody to follow");
    t.until = tick + 600; // keeps going until told otherwise
    const d = flat(q.location, p.location);
    const near = q.name === buddy ? Math.max(3.5, keep - 2) : 3.5; // protectors keep a respectful distance
    if (d > near) step(p, q.location.x, q.location.z, undefined, d > near + 5 ? RUN : WALK);
    else if (tick % 10 === 0) faceTo(p, q);
  },

  goto(p, t) {
    const q = t.player ? findPlayer(t.player) : undefined;
    const tgt = q ? q.location : t.x !== undefined ? { x: t.x, y: t.y ?? p.location.y, z: t.z } : undefined;
    if (!tgt) return finish("no idea where to go");
    if (flat(tgt, p.location) < (q ? 2.5 : 1.2)) return finish("arrived" + (q ? " at " + q.name : ""));
    step(p, tgt.x, tgt.z, undefined, flat(tgt, p.location) > 8 ? RUN : WALK);
  },

  wander(p, t) {
    if (!t.to) {
      const a = Math.random() * Math.PI * 2;
      const home = findPlayer()?.location ?? p.location;
      const far = others().length ? 12 : 32; // nobody about: explore further afield
      t.to = { x: home.x + Math.cos(a) * far, z: home.z + Math.sin(a) * far };
    }
    if (flat(t.to, p.location) < 1.5 || (t.stuck ?? 0) > 80) return finish("had a look around");
    step(p, t.to.x, t.to.z);
  },

  // --- mischief (a baby's): only ever aimed at companions, never at real players or builds -------------------
  snatch(p, t) {
    // toddle up to them, grab something they carry, then run off with it
    const q = world.getPlayers({ name: t.player ?? "" })[0];
    if (!q || !q.hasTag("companion") || q.name === p.name) return finish("no one to tease");
    if (flat(q.location, p.location) > 1.8) return void step(p, q.location.x, q.location.z, undefined, RUN);
    const c = inv(q);
    const slots = [];
    for (let i = 0; i < c.size; i++) if (c.getItem(i)) slots.push(i);
    if (!slots.length) return finish(`${q.name} had nothing to snatch`);
    const held = slots.includes(q.selectedSlotIndex) && Math.random() < 0.6 ? q.selectedSlotIndex : slots[Math.floor(Math.random() * slots.length)];
    const it = c.getItem(held);
    c.setItem(held, undefined);
    // into the baby's hand, so everyone sees what he's got
    const mine = inv(p);
    const left = mine.getItem(p.selectedSlotIndex);
    mine.setItem(p.selectedSlotIndex, it);
    if (left) mine.addItem(left);
    faceTo(p, q);
    swing(p);
    const l = p.location;
    p.dimension.runCommand(`playsound random.pop @a ${l.x} ${l.y} ${l.z} 1 1.6`);
    (SNATCHED[p.name] ??= []).push({ from: q.name, item: it.typeId, n: it.amount });
    // the victim gives chase at once (his brain catches up with what he says a moment later)
    setTask(q.name, { act: "chase", player: p.name, secs: 30 });
    tell(q.name, `${p.name} snatched your ${short(it.typeId)} (${it.amount}) and toddled off with it! Catch the baby to get it back.`);
    finish(`snatched ${it.amount} ${short(it.typeId)} from ${q.name}, hehe`);
  },

  run_away(p, t) {
    // scamper away from someone, giggling
    const q = world.getPlayers({ name: t.player ?? "" })[0];
    if (!q && !t.to) return finish("hehe");
    if (!t.to) {
      const a = Math.atan2(p.location.z - q.location.z, p.location.x - q.location.x) + (Math.random() - 0.5);
      t.to = { x: p.location.x + Math.cos(a) * 12, z: p.location.z + Math.sin(a) * 12 };
    }
    if (flat(t.to, p.location) < 1.5 || (t.stuck ?? 0) > 60) return finish(`ran away from ${q?.name ?? "danger"}`);
    step(p, t.to.x, t.to.z, undefined, WALK * 0.85); // little legs: grown-ups can catch him
  },

  distract(p, t) {
    // crawl right into their way: they lose track of what they were doing
    const q = world.getPlayers({ name: t.player ?? "" })[0];
    if (!q || !q.hasTag("companion") || q.name === p.name) return finish("no one to distract");
    if (flat(q.location, p.location) > 1.5) return void step(p, q.location.x, q.location.z, undefined, RUN);
    faceTo(p, q);
    const st = STATES.get(q.name);
    const was = st?.task ? describe(st.task) : "what you were doing";
    // he stops dead and stares at the baby for a moment
    setTask(q.name, { act: "stand", x: Math.floor(q.location.x), z: Math.floor(q.location.z), face: p.name, secs: 4, there: true });
    tell(q.name, `${p.name} crawled right into your way and you completely lost track of ${was}!`);
    finish(`distracted ${q.name}, hehe`);
  },

  sing(p, t) {
    // start a show: the band lines up beside the singer, facing the audience, and the song plays from here
    if (tick - lastShow < 1200) return finish("we just sang! (one show a minute at most)");
    lastShow = tick;
    const fans = others().sort((a, b) => dist(a.location, p.location) - dist(b.location, p.location));
    const fan = fans[0];
    const band = (Array.isArray(t.band) ? t.band : [p.name]).filter((n) => world.getPlayers({ name: n })[0]);
    const secs = Math.min(Number(t.secs) || 18, 40);
    band.forEach((name, i) => {
      const spot = { x: Math.floor(p.location.x) + (i - Math.floor(band.length / 2)) * 2, z: Math.floor(p.location.z) };
      const job = { act: "perform", x: spot.x, z: spot.z, face: fan?.name, secs: secs + 4, startAt: tick + 60 };
      if (name === p.name) {
        Object.assign(t, job, { act: "perform", until: tick + (secs + 4) * 20 });
      } else setTask(name, job);
    });
    const l = p.location;
    system.runTimeout(() => {
      try {
        p.dimension.runCommand(`playsound pixelpops.${String(t.song).replace(/[^a-z_]/g, "")} @a ${l.x} ${l.y + 1} ${l.z} 1.6 1`);
      } catch (e) {
        out({ error: "song: " + e });
      }
    }, 60);
    out({ result: `started the show: ${t.song}` });
  },

  perform(p, t) {
    // get to my spot, then bounce and sway to the beat (124 bpm: about every 10 ticks) until the song ends
    const at = p.location;
    if (!t.there && Math.hypot(t.x + 0.5 - at.x, t.z + 0.5 - at.z) > 0.3 && tick < (t.startAt ?? 0) + 40) {
      step(p, t.x + 0.5, t.z + 0.5, undefined, RUN);
      return;
    }
    t.there = true;
    const gy = groundY(p.dimension, at.x, at.z, at.y) ?? Math.floor(at.y);
    const fan = world.getPlayers({ name: t.face ?? "" })[0];
    const beat = Math.floor(tick / 10) % 4;
    const sway = beat === 1 ? 0.6 : beat === 3 ? -0.6 : 0;
    const look = fan ? { x: fan.location.x + sway, y: fan.location.y + 1.5, z: fan.location.z } : undefined;
    const hop = tick % 10 < 4 ? 0.45 : 0;
    p.teleport({ x: at.x, y: gy + hop, z: at.z }, look ? { facingLocation: look } : undefined);
    if (tick % 20 === 0) {
      swing(p); // an arm wave
    }
    if (tick >= t.until - 2) finish("what a show!");
  },

  taxi(p, t) {
    // Kostas drives his taxi: it appears by him if it isn't around, he takes the wheel, then it drives along a
    // planned route to the place or player asked for (passengers ride along). Script-moved, so it glides smoothly.
    const dim = p.dimension;
    let car = dim.getEntities({ type: "companion:taxi", location: p.location, maxDistance: 64 })[0];
    if (!car) {
      try {
        car = dim.spawnEntity("companion:taxi", { x: p.location.x + 2, y: p.location.y, z: p.location.z });
        car.nameTag = "TAXI";
      } catch (e) {
        return finish("the taxi isn't here yet (it arrives after the server restarts)");
      }
    }
    if (p.getComponent("minecraft:riding")?.entityRidingOn?.id !== car.id) {
      if (flat(car.location, p.location) > 3) return void step(p, car.location.x, car.location.z, undefined, RUN);
      car.getComponent("minecraft:rideable")?.addRider(p);
      return;
    }
    if (t.wait && tick < t.wait) return; // give the passenger a moment to hop in
    const q = t.player ? world.getPlayers({ name: t.player })[0] : undefined;
    const to = q ? q.location : Number.isFinite(t.x) ? { x: t.x, y: t.y ?? car.location.y, z: t.z } : undefined;
    if (!to) return finish("ready to go: where to?");
    const at = car.location;
    if (flat(to, at) < 3) return finish("arrived" + (q ? " at " + q.name : "") + ", that's the ride!");
    if (!t.path || tick - (t.planned ?? 0) > 200 || (q && flat(q.location, t.goal ?? q.location) > 6)) {
      t.search ??= startSearch(dim, { x: Math.floor(at.x), y: Math.floor(at.y + 0.01), z: Math.floor(at.z) },
        { x: Math.floor(to.x), y: Math.floor(to.y), z: Math.floor(to.z) });
      const path = stepSearch(dim, t.search, 600);
      if (!path) return;
      t.search = undefined;
      t.path = path;
      t.i = 1;
      t.planned = tick;
      t.goal = { ...to };
    }
    if (t.i >= t.path.length) {
      t.path = undefined; // end of what we could plan: plan again from here
      return;
    }
    const w = t.path[t.i];
    const d = Math.hypot(w.x - at.x, w.z - at.z);
    const sp = 0.35; // about 7 blocks a second: quicker than running
    if (d <= sp) {
      car.teleport({ x: w.x, y: w.y, z: w.z }, { facingLocation: { x: w.x + (w.x - at.x), y: w.y + 1, z: w.z + (w.z - at.z) } });
      t.i++;
    } else {
      const nx = at.x + ((w.x - at.x) / d) * sp;
      const nz = at.z + ((w.z - at.z) / d) * sp;
      const ny = at.y + Math.max(-0.5, Math.min(0.5, w.y - at.y));
      car.teleport({ x: nx, y: ny, z: nz }, { facingLocation: { x: w.x, y: ny + 1, z: w.z } });
    }
    if (tick % 100 === 0) dim.runCommand(`playsound note.bass @a ${at.x} ${at.y} ${at.z} 0.6 0.5`); // the engine purring
  },

  park(p, t) {
    // stop the taxi, Kostas gets out, passengers too
    const car = p.dimension.getEntities({ type: "companion:taxi", location: p.location, maxDistance: 16 })[0];
    car?.getComponent("minecraft:rideable")?.ejectRiders();
    finish("parked");
  },

  // --- chests ---
  store(p, t) {
    // put things in a chest nearby (one is placed if there's none and we carry one); tools, weapons, food,
    // buckets and torches stay with us unless named
    let chest = nearBlock(p, /^minecraft:chest$/, 5);
    if (!chest) {
      if (count(p, "chest") === 0) return finish("no chest here (craft one: 8 planks)");
      RUNNERS.place(p, { item: "chest" });
      chest = nearBlock(p, /^minecraft:chest$/, 3);
      if (!chest) return finish("couldn't put a chest down here");
      task = t; // place() finished the task: carry on with storing
    }
    const box = chest.getComponent("minecraft:inventory")?.container;
    if (!box) return finish("that chest won't open");
    const keep = /_(pickaxe|axe|sword|shovel)$|bow$|arrow$|bucket$|torch$|flint_and_steel$|shield$|ender_eye$/;
    const want = Array.isArray(t.items) ? t.items.map((x) => "minecraft:" + String(x).replace("minecraft:", "")) : null;
    const c = inv(p);
    let moved = 0;
    for (let i = 0; i < c.size; i++) {
      const it = c.getItem(i);
      if (!it) continue;
      if (want ? !want.includes(it.typeId) : keep.test(it.typeId) || FOOD[short(it.typeId)] !== undefined) continue;
      const left = box.addItem(it);
      c.setItem(i, left);
      moved += it.amount - (left?.amount ?? 0);
    }
    swing(p);
    finish(`stored ${moved} items in the chest at ${chest.location.x} ${chest.location.y} ${chest.location.z}`);
  },

  take(p, t) {
    // take something out of a chest nearby
    const chest = nearBlock(p, /^minecraft:chest$/, 5);
    if (!chest) return finish("no chest close by");
    const box = chest.getComponent("minecraft:inventory")?.container;
    const id = "minecraft:" + String(t.item ?? "").replace("minecraft:", "");
    let need = Math.max(1, Number(t.count ?? t.n) || 64);
    let got = 0;
    for (let i = 0; box && i < box.size && need > 0; i++) {
      const it = box.getItem(i);
      if (!it || (t.item && it.typeId !== id)) continue;
      const k = Math.min(need, it.amount);
      add(p, it.typeId, k);
      if (k === it.amount) box.setItem(i, undefined);
      else {
        it.amount -= k;
        box.setItem(i, it);
      }
      need -= k;
      got += k;
    }
    swing(p);
    finish(got ? `took ${got} ${t.item ?? "things"} from the chest` : `no ${t.item ?? "things"} in the chest`);
  },

  // --- the bow ---
  shoot(p, t) {
    // shoot arrows at a mob from a distance: needs a bow and arrows (each shot uses one)
    if (count(p, "bow") === 0) return finish("I need a bow (3 sticks + 3 string)");
    if (count(p, "arrow") === 0) return finish("out of arrows");
    let e = t.target;
    if (!e || !e.isValid) {
      const want = t.mob && t.mob !== "hostile" ? "minecraft:" + t.mob.replace("minecraft:", "") : undefined;
      e = p.dimension.getEntities({ location: p.location, maxDistance: 28, ...(want ? { type: want } : { families: ["monster"] }) })
        .filter((x) => x.typeId !== "minecraft:player")
        .sort((a, b) => dist(a.location, p.location) - dist(b.location, p.location))[0];
      if (!e) return finish(t.done ? `shot down ${t.done} ${t.mob ?? "monsters"}` : `nothing to shoot`);
      if (t.target) t.done++;
      if (t.done >= t.n) return finish(`shot down ${t.done} ${t.mob ?? "monsters"}`);
      t.target = e;
    }
    const d = dist(e.location, p.location);
    if (d > 24) return void step(p, e.location.x, e.location.z, e.location, RUN);
    faceTo(p, e);
    if (tick - (t.shotAt ?? -99) < 24) return; // drawing the bow
    t.shotAt = tick;
    const eye = { x: p.location.x, y: p.location.y + 1.5, z: p.location.z };
    const to = { x: e.location.x - eye.x, y: e.location.y + 1 - eye.y, z: e.location.z - eye.z };
    const len = Math.hypot(to.x, to.y, to.z) || 1;
    const dir = { x: to.x / len, y: to.y / len + 0.04 * (len / 8), z: to.z / len };
    try {
      const arrow = p.dimension.spawnEntity("minecraft:arrow", { x: eye.x + dir.x, y: eye.y + dir.y, z: eye.z + dir.z });
      const proj = arrow.getComponent("minecraft:projectile");
      proj.owner = p;
      proj.shoot({ x: dir.x * 2.6, y: dir.y * 2.6, z: dir.z * 2.6 });
      take(p, "arrow", 1);
      swing(p);
    } catch (err) {
      return finish("the bow jammed: " + err);
    }
  },

  // --- the Nether portal ---
  portal(p, t) {
    // build a Nether portal frame 2 blocks ahead (10 obsidian: from our pack, or cast on the spot from a lava
    // bucket + a water bucket, the speedrunners' bucket portal), then light it with flint and steel
    const dim = p.dimension;
    if (!t.frame) {
      const f = { x: Math.floor(p.location.x), y: Math.floor(p.location.y + 0.01), z: Math.floor(p.location.z) };
      const zf = f.z + 2;
      const x0 = f.x - 1;
      const frame = [[x0 + 1, f.y - 1], [x0 + 2, f.y - 1], [x0 + 1, f.y + 3], [x0 + 2, f.y + 3]];
      for (let k = 0; k < 3; k++) frame.push([x0, f.y + k], [x0 + 3, f.y + k]);
      const inside = [[x0 + 1, f.y], [x0 + 2, f.y], [x0 + 1, f.y + 1], [x0 + 2, f.y + 1], [x0 + 1, f.y + 2], [x0 + 2, f.y + 2]];
      // clear the inside like a player would (never someone's build: breakAt refuses those)
      if (!inside.every(([x, y]) => soft(blockAt(dim, x, y, zf)) || breakAt(dim, x, y, zf)))
        return finish("not enough open space for a portal here (needs 2 wide, 3 tall)");
      t.frame = frame.map(([x, y]) => ({ x, y, z: zf }));
      t.fire = { x: x0 + 1, y: f.y, z: zf };
      t.k = 0;
    }
    if (tick - (t.at ?? -99) < 10) return;
    t.at = tick;
    while (t.k < t.frame.length) {
      const c = t.frame[t.k];
      const b = blockAt(dim, c.x, c.y, c.z);
      if (b?.typeId === "minecraft:obsidian") {
        t.k++;
        continue;
      }
      if (!b || (!soft(b) && !breakAt(dim, c.x, c.y, c.z))) return finish("something's in the way of the portal frame");
      if (count(p, "obsidian") > 0) take(p, "obsidian", 1);
      else if (count(p, "lava_bucket") > 0 && count(p, "water_bucket") > 0) {
        take(p, "lava_bucket", 1); // pour lava into the mould, cool it with water: obsidian
        add(p, "minecraft:bucket", 1);
      } else return finish(`frame ${t.k}/10 done: I need ${10 - t.k} more obsidian (or a lava bucket each, with a water bucket)`);
      faceTo(p, { x: c.x + 0.5, y: c.y + 0.5, z: c.z + 0.5 });
      swing(p);
      blockAt(dim, c.x, c.y, c.z)?.setType("minecraft:obsidian");
      OWNED.add(`${c.x},${c.y},${c.z}`);
      t.k++;
      return;
    }
    if (count(p, "flint_and_steel") === 0) return finish("the portal frame is built: I need flint and steel to light it (1 iron + 1 flint)");
    const fire = blockAt(dim, t.fire.x, t.fire.y, t.fire.z);
    faceTo(p, { x: t.fire.x + 0.5, y: t.fire.y + 0.5, z: t.fire.z + 0.5 });
    swing(p);
    fire?.setType("minecraft:fire");
    system.runTimeout(() => {
      const lit = /portal/.test(blockAt(dim, t.fire.x, t.fire.y + 1, t.fire.z)?.typeId ?? "");
      out({ result: lit ? `the Nether portal is lit at ${t.fire.x} ${t.fire.y} ${t.fire.z}!` : "lit the frame but no portal formed" });
    }, 10);
    finish("lighting the portal");
  },

  enter_portal(p, t) {
    // walk into a lit portal and stand still in it until it takes us through (about 4 seconds)
    if (!t.cell) {
      const b = findDeep(p, /^minecraft:portal$/, 16, -4, 4);
      if (!b) return finish("no lit portal close by");
      let y = b.location.y; // stand in the bottom portal block, where our feet go
      while (/portal/.test(blockAt(p.dimension, b.location.x, y - 1, b.location.z)?.typeId ?? "")) y--;
      t.cell = { x: b.location.x + 0.5, y, z: b.location.z + 0.5 };
      t.from = p.dimension.id;
      // stand a step in front of the portal (on our side), then walk straight in
      const alongX = /portal/.test(blockAt(p.dimension, b.location.x + 1, y, b.location.z)?.typeId ?? "") ||
        /portal/.test(blockAt(p.dimension, b.location.x - 1, y, b.location.z)?.typeId ?? "");
      const side = alongX ? Math.sign(p.location.z - t.cell.z) || -1 : Math.sign(p.location.x - t.cell.x) || -1;
      t.front = alongX ? { x: t.cell.x, y, z: t.cell.z + 1.6 * side } : { x: t.cell.x + 1.6 * side, y, z: t.cell.z };
    }
    if (p.dimension.id !== t.from) return finish(`went through the portal: now in ${p.dimension.id.replace("minecraft:", "")}`);
    const inside = flat(t.cell, p.location) < 0.5 && Math.abs(p.location.y - t.cell.y) < 1;
    if (inside) return void controls(p, { f: 0 });
    if (!t.lined && flat(t.front, p.location) < 0.6 && Math.abs(p.location.y - t.cell.y) < 1) t.lined = true;
    if (!t.lined) return void step(p, t.front.x, t.front.z, t.front);
    if (t.cell.y > p.location.y + 0.4 && flat(t.cell, p.location) < 1.6) {
      // the portal sits a step up: hop in, like a player
      return void controls(p, { f: 1, j: 1, y: yawTo(p.location, t.cell), p: 0 });
    }
    step(p, t.cell.x, t.cell.z, t.cell);
  },

  chase(p, t) {
    // run after someone until we catch them
    const q = world.getPlayers({ name: t.player ?? "" })[0];
    if (!q) return finish("lost them");
    if (flat(q.location, p.location) < 1.3) return finish(`caught ${q.name}!`);
    step(p, q.location.x, q.location.z, undefined, RUN);
  },

  fill(p, t) {
    // scoop a lava or water source into an empty bucket, like a player: walk up, look, use the bucket
    const what = /water/.test(t.item ?? t.block ?? "lava") ? "water" : "lava";
    if (count(p, "bucket") === 0) return finish("I need an empty bucket (3 iron ingots at a crafting table)");
    if (!t.at && Number.isFinite(t.x) && Number.isFinite(t.y) && Number.isFinite(t.z)) t.at = { x: Math.floor(t.x), y: Math.floor(t.y), z: Math.floor(t.z) };
    if (!t.at) {
      // lava is usually deep: look well below us and further out (once per try, it's a big search)
      const src = findDeep(p, new RegExp("^minecraft:" + what + "$"), 20, -20, 6);
      // a source block is "level 0"; flowing bits can't be scooped
      if (!src) return finish(`no ${what} close by`);
      t.at = src.location;
    }
    const c = { x: t.at.x + 0.5, y: t.at.y + 0.5, z: t.at.z + 0.5 };
    if (dist(c, { x: p.location.x, y: p.location.y + 1.6, z: p.location.z }) > REACH) return void step(p, c.x, c.z, c);
    faceTo(p, c);
    swing(p);
    const b = blockAt(p.dimension, t.at.x, t.at.y, t.at.z);
    let level;
    try {
      level = b?.permutation?.getState("liquid_depth");
    } catch {}
    if (!b || !new RegExp(what).test(b.typeId) || (level !== undefined && level !== 0)) {
      t.at = undefined;
      t.tries = (t.tries ?? 0) + 1;
      if (t.tries > 8) finish(`couldn't find a ${what} source block to scoop`);
      return;
    }
    b.setType("minecraft:air");
    take(p, "bucket", 1);
    add(p, `minecraft:${what}_bucket`, 1);
    finish(`filled a bucket with ${what}`);
  },

  stand(p, t) {
    // go to an exact spot (a place in a line-up) and stay there, facing someone
    const at = p.location;
    if (Math.hypot(t.x + 0.5 - at.x, t.z + 0.5 - at.z) > 0.25 && !t.there) {
      if (!step(p, t.x + 0.5, t.z + 0.5) && (t.stuck ?? 0) > 120) t.there = true; // as close as we can get
      return;
    }
    t.there = true;
    const q = world.getPlayers({ name: t.face ?? "" })[0];
    if (q && tick % 10 === 0) faceTo(p, q);
  },

  stay(p, t) {
    if (tick + 1 >= t.until) return finish("waited");
    if (tick % 20 === 0) {
      const q = others().sort((a, b) => dist(a.location, p.location) - dist(b.location, p.location))[0];
      if (q && dist(q.location, p.location) < 16) faceTo(p, q);
    }
  },

  attack(p, t) {
    let e = t.target;
    if (!e || !e.isValid) {
      if (t.target) {
        t.done++;
        t.target = undefined;
        if (t.done >= t.n) return finish(`got the ${t.mob}`);
      }
      const want = t.mob && t.mob !== "hostile" ? "minecraft:" + t.mob.replace("minecraft:", "") : undefined;
      const near = p.dimension
        .getEntities({ location: p.location, maxDistance: 24, ...(want ? { type: want } : { families: ["monster"] }) })
        .filter((x) => x.typeId !== "minecraft:player")
        .sort((a, b) => dist(a.location, p.location) - dist(b.location, p.location))[0];
      if (!near) return finish(t.done ? `got ${t.done} ${t.mob}` : `no ${t.mob ?? "monster"} nearby`);
      t.target = e = near;
      t.mob = short(near.typeId);
    }
    hold(p, "sword") ?? hold(p, "axe");
    const d = dist({ ...e.location, y: e.location.y + 0.9 }, { ...p.location, y: p.location.y + 1.6 });
    if (d > 3.2) {
      if (flat(e.location, p.location) < 0.8) return; // right above or below us: the escape logic deals with it
      return step(p, e.location.x, e.location.z, e.location, RUN);
    }
    faceTo(p, e);
    if (tick - (t.hitAt ?? -99) >= 12) {
      t.hitAt = tick;
      swing(p);
      e.applyDamage(weaponDamage(p), { cause: EntityDamageCause.entityAttack, damagingEntity: p });
      lootHere(p.name, { ...e.location }); // what it drops is ours
    }
  },

  mine(p, t) {
    if (t.end) return finish(t.end); // after walking over to collect the last drops
    const kind = MINE[t.block] ?? { m: matcher(t.block), tool: "", hand: 30, need: -1 };
    if (!t.at) {
      t.bad ??= new Set(); // ones we found we can't get at (a build in the way): don't pick them again
      const b = findBlock(p, kind.m, 16, t.bad);
      if (!b) return finish(t.done ? `mined ${t.done} ${t.block}, no more close by` : `can't see any ${t.block} close by`);
      t.at = b.location;
      t.timer = undefined;
    }
    const c = { x: t.at.x + 0.5, y: t.at.y + 0.5, z: t.at.z + 0.5 };
    const eye = { x: p.location.x, y: p.location.y + 1.6, z: p.location.z };
    const near = dist(c, eye) <= REACH;
    if (t.dig || !near || !isOpen(p.dimension, t.at)) {
      // walk over; when walls or ground are in the way, dig through like a player would
      if (!t.dig && !near && flat(c, p.location) > 1 && (t.stuck ?? 0) < 15 && step(p, c.x, c.z, c)) return;
      if (c.y > p.location.y + 3.5 && !near) {
        t.at = undefined; // too high up: pick another
        t.skips = (t.skips ?? 0) + 1;
        if (t.skips > 6) finish(`couldn't reach any ${t.block}`);
        return;
      }
      const r = dig(p, t, c);
      if (r && /build is in the way|too hard|lava/.test(r) && (t.skips ?? 0) < 6) {
        t.bad.add(`${t.at.x},${t.at.y},${t.at.z}`);
        t.at = undefined; // that one's off limits: try another
        t.skips = (t.skips ?? 0) + 1;
        return;
      }
      if (r) finish(r);
      return;
    }
    const b = blockAt(p.dimension, t.at.x, t.at.y, t.at.z);
    if (!b || !kind.m.test(b.typeId)) {
      t.at = undefined;
      return;
    }
    if (t.timer === undefined) {
      const tier = kind.tool ? hold(p, kind.tool) : undefined;
      if (kind.need >= 0 && (tier === undefined || TIERS.indexOf(tier) > kind.need))
        return finish(`I need a ${kind.need >= 5 ? "" : TIERS[kind.need] + " "}pickaxe to mine ${t.block}`);
      t.timer = Math.round(kind.hand / (tier ? SPEEDUP[tier] : 1) / (p.hasTag("strong") ? 3 : 1));
    }
    if (tick % 5 === 0) {
      faceTo(p, c);
      swing(p);
    }
    if (--t.timer > 0) return;
    if (!breakAt(p.dimension, t.at.x, t.at.y, t.at.z)) {
      t.at = undefined; // someone's build: leave it
      return;
    }
    t.done++;
    t.walkTo = { x: c.x, z: c.z, until: tick + 30 };
    t.at = undefined;
    if (t.done >= t.n) t.end = `mined ${t.done} ${t.block}`;
  },

  give(p, t) {
    const q = findPlayer(t.player);
    if (!q) return finish("nobody to give to");
    const item = t.item.replace("minecraft:", "");
    if (count(p, item) === 0) return finish(`I don't have any ${item}`);
    if (flat(q.location, p.location) > 2.5) return step(p, q.location.x, q.location.z, undefined, RUN);
    faceTo(p, q);
    const n = Math.min(t.n, count(p, item));
    const id = take(p, item, n);
    p.dimension.spawnItem(new ItemStack(id, n), q.location);
    finish(`gave ${q.name} ${n} ${item}`);
  },

  craft(p, t) {
    finish(craft(p, t.item, t.n));
  },

  build(p, t) {
    // place blocks one at a time where asked, like a player: walk within reach, need something to place
    // against, need the block in the inventory. Doors take two blocks of height.
    if (!t.queue) {
      t.queue = cells(t).slice(0, 160);
      t.item = String(t.item ?? "").replace("minecraft:", "");
      t.placed = 0;
      t.skip = { taken: 0, floating: 0, unreachable: 0, already: 0 };
      t.retried = new Set();
      if (!t.queue.length) return finish("build needs blocks: a list, or a shape with from and to");
    }
    if (t.cool && tick < t.cool) return;
    const pos = t.queue[0];
    if (!pos) {
      const s = Object.entries(t.skip).filter(([, v]) => v).map(([k, v]) => `${v} ${k}`).join(", ");
      return finish(`placed ${t.placed} ${t.item}${s ? " (skipped: " + s + ")" : ""}`);
    }
    if (count(p, t.item) === 0) return finish(`ran out of ${t.item} after placing ${t.placed}; ${t.queue.length} still to go`);
    const [x, y, z] = pos.map(Math.floor);
    const dim = p.dimension;
    const b = blockAt(dim, x, y, z);
    const done = (why) => {
      t.queue.shift();
      if (why) t.skip[why]++;
    };
    if (!b) return done("unreachable");
    if (b.typeId === "minecraft:" + t.item) return done("already");
    if (!soft(b) || /torch/.test(b.typeId)) {
      // natural ground in the way (grass, dirt, sand, stone...): dig it out first, like a player levelling a site
      const kind = !BUILT.test(b.typeId) && Object.values(MINE).find((k) => k.m.test(b.typeId));
      if (!kind || /_log$|_stem$/.test(b.typeId)) return done("taken");
      const c0 = { x: x + 0.5, y: y + 0.5, z: z + 0.5 };
      if (dist(c0, { x: p.location.x, y: p.location.y + 1.6, z: p.location.z }) > REACH) return void step(p, c0.x, c0.z, c0);
      if (t.clear === undefined) {
        const tier = kind.tool ? hold(p, kind.tool) : undefined;
        if (kind.need >= 0 && (tier === undefined || TIERS.indexOf(tier) > kind.need)) return done("taken");
        t.clear = Math.round(kind.hand / (tier ? SPEEDUP[tier] : 1));
      }
      if (tick % 5 === 0) {
        faceTo(p, c0);
        swing(p);
      }
      if (--t.clear > 0) return;
      t.clear = undefined;
      if (!breakAt(dim, x, y, z)) return done("taken");
      return; // next tick places into the cleared spot
    }
    const door = /_door$/.test(t.item);
    if (door && !soft(blockAt(dim, x, y + 1, z))) return done("taken");
    // something to place it against (a real player can't place a block in mid-air)
    const support = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]].some(([a, c, e]) => {
      const n = blockAt(dim, x + a, y + c, z + e);
      return n && !soft(n);
    });
    if (!inSite(x, y, z) && nearPlayerBuild(x, y, z)) return done("taken"); // not in someone else's house
    if (!support || (door && !floorOk(blockAt(dim, x, y - 1, z)))) {
      // maybe a later block in the list holds it up: try it once more at the end
      const key = x + "," + y + "," + z;
      t.queue.shift();
      if (!t.retried.has(key)) {
        t.retried.add(key);
        t.queue.push(pos);
      } else t.skip.floating++;
      return;
    }
    const c = { x: x + 0.5, y: y + 0.5, z: z + 0.5 };
    const eye = { x: p.location.x, y: p.location.y + 1.6, z: p.location.z };
    // never place a block where someone is standing (ourselves included)
    const inCell = (e) => Math.floor(e.location.x) === x && Math.floor(e.location.z) === z && y >= Math.floor(e.location.y + 0.01) && y <= Math.floor(e.location.y + 0.01) + 1;
    if (inCell(p)) {
      // step out of the way: to the side away from the block
      const away = groundY(dim, x + 1.5, z + 0.5, p.location.y) !== undefined ? [x + 1.5, z + 0.5] : [x - 0.5, z + 0.5];
      step(p, away[0], away[1], c);
      return;
    }
    if (world.getPlayers().some((q) => q.name !== p.name && inCell(q))) {
      t.cool = tick + 20; // wait for them to move
      return;
    }
    if (dist(c, eye) > REACH) {
      if (!step(p, c.x, c.z, c) && (t.stuck ?? 0) > 80) {
        t.stuck = 0;
        done("unreachable");
      }
      return;
    }
    faceTo(p, c);
    swing(p);
    take(p, t.item, 1);
    if (door) {
      const id = "minecraft:" + t.item;
      b.setPermutation(BlockPermutation.resolve(id, { upper_block_bit: false }));
      blockAt(dim, x, y + 1, z)?.setPermutation(BlockPermutation.resolve(id, { upper_block_bit: true }));
    } else b.setType("minecraft:" + t.item);
    OWNED.add(`${x},${y},${z}`);
    t.placed++;
    t.queue.shift();
    t.cool = tick + 5; // a player places about four blocks a second
  },

  dig(p, t) {
    // break particular blocks (to fix a mistake or make a doorway); drops are picked up as usual
    if (!t.queue) {
      t.queue = cells(t).slice(0, 64);
      t.broken = 0;
      if (!t.queue.length) return finish("dig needs blocks: a list, or a shape with from and to");
    }
    const pos = t.queue[0];
    if (!pos) return finish(`broke ${t.broken} blocks${t.protected ? `; left ${t.protected} alone (part of a player's build)` : ""}`);
    const [x, y, z] = pos.map(Math.floor);
    const b = blockAt(p.dimension, x, y, z);
    if (!b || soft(b)) {
      t.queue.shift();
      t.timer = undefined;
      return;
    }
    if (/bedrock|obsidian|barrier/.test(b.typeId)) {
      t.queue.shift();
      return;
    }
    const c = { x: x + 0.5, y: y + 0.5, z: z + 0.5 };
    const eye = { x: p.location.x, y: p.location.y + 1.6, z: p.location.z };
    if (dist(c, eye) > REACH) return void step(p, c.x, c.z, c);
    if (t.timer === undefined) {
      const kind = Object.values(MINE).find((k) => k.m.test(b.typeId)) ?? { tool: /_planks|_door|chest|crafting/.test(b.typeId) ? "axe" : "", hand: /glass|torch/.test(b.typeId) ? 6 : 30, need: -1 };
      const tier = kind.tool ? hold(p, kind.tool) : undefined;
      if (kind.need >= 0 && (tier === undefined || TIERS.indexOf(tier) > kind.need)) return finish(`I need a pickaxe to break ${short(b.typeId)}`);
      t.timer = Math.round(kind.hand / (tier ? SPEEDUP[tier] : 1) / (p.hasTag("strong") ? 3 : 1));
    }
    if (tick % 5 === 0) {
      faceTo(p, c);
      swing(p);
    }
    if (--t.timer > 0) return;
    if (breakAt(p.dimension, x, y, z)) t.broken++;
    else t.protected = (t.protected ?? 0) + 1;
    t.timer = undefined;
    t.queue.shift();
  },

  place(p, t) {
    const item = t.item.replace("minecraft:", "");
    if (count(p, item) === 0) return finish(`I don't have a ${item}`);
    if (/door|bed$/.test(item)) return finish(`give the ${item} to someone to put down instead`);
    const at = p.location;
    for (const [dx, dz] of [[1, 0], [0, 1], [-1, 0], [0, -1], [1, 1], [-1, -1], [1, -1], [-1, 1]]) {
      const x = Math.floor(at.x) + dx;
      const z = Math.floor(at.z) + dz;
      const y = Math.floor(at.y);
      const b = blockAt(p.dimension, x, y, z);
      if (!b?.isAir || !floorOk(blockAt(p.dimension, x, y - 1, z))) continue;
      if (item !== "torch" && nearPlayerBuild(x, y, z)) return finish(`not putting a ${item} in someone's build`);
      take(p, item, 1);
      b.setType("minecraft:" + item);
      OWNED.add(`${x},${y},${z}`);
      return finish(`put down a ${item}`);
    }
    finish(`no room to put the ${item}`);
  },

  eat(p, t) {
    const h = p.getComponent("minecraft:health");
    const food = Object.keys(FOOD).sort((a, b) => FOOD[b] - FOOD[a]).find((f) => count(p, f) > 0);
    if (!food) return finish("I have no food");
    take(p, food, 1);
    h.setCurrentValue(Math.min(h.effectiveMax, h.currentValue + FOOD[food]));
    finish(`ate ${food}`);
  },

  cook(p, t) {
    // a furnace has to be right here, and fuel; takes as long as a real one (10s per item)
    if (!nearBlock(p, /furnace$/, 4)) return finish("I need to stand by a furnace to cook");
    const raw = Object.keys(SMELT).find((k) => count(p, k) > 0 && (!t.item || t.item.replace("minecraft:", "") === k));
    if (!raw) return finish(t.done ? `cooked ${t.done}` : "nothing to cook");
    if (count(p, "coal") === 0 && count(p, "planks") === 0 && count(p, "log") === 0) return finish("no fuel for the furnace");
    t.timer ??= 200;
    if (--t.timer > 0) return;
    t.timer = 200;
    take(p, raw, 1);
    if ((t.done ?? 0) % 8 === 0) take(p, count(p, "coal") ? "coal" : count(p, "planks") ? "planks" : "log", 1);
    add(p, "minecraft:" + SMELT[raw], 1);
    t.done++;
    if (t.done >= t.n) finish(`cooked ${t.done} ${raw}`);
  },
};
// every game action in actions.json has a runner here, and every runner is listed there (test/test_wiring.py checks
// this too, without a server)
for (const [name, a] of Object.entries(ACTIONS))
  if (a.runs_in === "game" && !RUNNERS[name]) console.warn(`[claude] actions.json lists "${name}" but main.js has no runner`);
for (const name of Object.keys(RUNNERS))
  if (ACTIONS[name]?.runs_in !== "game") console.warn(`[claude] runner "${name}" isn't in actions.json`);

/** One digging step toward c: a tunnel (two blocks high), or a staircase down when c is below us.
 *  Returns a message when it can't go on. */
function dig(p, t, c) {
  const dim = p.dimension;
  if (!t.dig) {
    const f = { x: Math.floor(p.location.x), y: Math.floor(p.location.y + 0.01), z: Math.floor(p.location.z) };
    const dx = c.x - (f.x + 0.5);
    const dz = c.z - (f.z + 0.5);
    let sx = 0;
    let sz = 0;
    if (Math.abs(dx) >= Math.abs(dz) && Math.abs(dx) > 0.6) sx = Math.sign(dx);
    else if (Math.abs(dz) > 0.6) sz = Math.sign(dz);
    else [sx, sz] = t.stairDir ?? (t.stairDir = [1, 0]); // straight below: stairs, never a pit
    const down = c.y < f.y - 0.5;
    const nx = f.x + sx;
    const nz = f.z + sz;
    const cells = down ? [[nx, f.y + 1, nz], [nx, f.y, nz], [nx, f.y - 1, nz]] : [[nx, f.y + 1, nz], [nx, f.y, nz]];
    const hard = cells.map(([x, y, z]) => blockAt(dim, x, y, z)).find((b) => b && !soft(b) && !(b.location.x === t.at.x && b.location.y === t.at.y && b.location.z === t.at.z));
    if (!hard) {
      // the way is open (or the next block is the one we want): step in
      t.walkTo = { x: nx + 0.5, z: nz + 0.5, until: tick + 15 };
      t.stuck = 0;
      return;
    }
    if (/bedrock|obsidian|barrier|reinforced/.test(hard.typeId)) return `something too hard is in the way to the ${t.block}`;
    if (protectedAt(dim, hard.location.x, hard.location.y, hard.location.z)) return `someone's build is in the way to the ${t.block}`;
    if (isNear(dim, hard.location, /lava/)) return `there's lava by the ${t.block}, not digging there`;
    const kind = Object.values(MINE).find((k) => k.m.test(hard.typeId)) ?? { tool: "", hand: 20, need: -1 };
    const tier = kind.tool ? hold(p, kind.tool) : undefined;
    if (kind.need >= 0 && (tier === undefined || TIERS.indexOf(tier) > kind.need))
      return `I need a pickaxe to dig through to the ${t.block}`;
    t.dig = { ...hard.location, timer: Math.round(kind.hand / (tier ? SPEEDUP[tier] : 1)) };
  }
  if (tick % 5 === 0) {
    faceTo(p, { x: t.dig.x + 0.5, y: t.dig.y + 0.5, z: t.dig.z + 0.5 });
    swing(p);
  }
  if (--t.dig.timer > 0) return;
  breakAt(p.dimension, t.dig.x, t.dig.y, t.dig.z);
  t.dig = undefined;
}

const isNear = (dim, l, m) =>
  [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]].some(([a, c, e]) => m.test(blockAt(dim, l.x + a, l.y + c, l.z + e)?.typeId ?? ""));

// --- getting out of holes and up walls ---------------------------------------------------------------

const PILLAR = /^minecraft:(dirt|cobblestone|cobbled_deepslate|stone|\w+_planks|netherrack|coarse_dirt|andesite|diorite|granite|tuff|deepslate)$/;

/** Watch whether we get anywhere. Returns true while we're busy escaping (nothing else should move us). */
function unstick(p) {
  const at = p.location;
  if (!progress || flat(progress, at) > 1.2 || Math.abs(progress.y - at.y) > 0.9) progress = { x: at.x, y: at.y, z: at.z, tick };
  const trying = goal && tick - goal.tick < 3 && (flat(goal, at) > 2 || goal.y > at.y + 2.5);
  if (!trying) progress.tick = tick;
  if (!escape && tick - progress.tick > 50) {
    escape = { tries: 0, until: tick + 400, goal: { ...goal } };
    if (tick - (STUCKSAID[p.name] ?? -9999) > 1200) {
      STUCKSAID[p.name] = tick; // say so once a minute at most
      out({ reflex: "stuck, climbing out" });
    }
  }
  if (!escape) return false;
  if (tick > escape.until || escape.tries > 30) {
    escape = null;
    progress.tick = tick;
    last = "I got stuck and couldn't climb out; I need a pickaxe or some blocks";
    return false;
  }
  climbStep(p);
  return true;
}

/** One move of a staircase up toward the goal: clear head room, dig a step, step up; or build up under ourselves. */
function climbStep(p) {
  const dim = p.dimension;
  const e = escape;
  if (e.dig) {
    if (tick % 5 === 0) {
      faceTo(p, { x: e.dig.x + 0.5, y: e.dig.y + 0.5, z: e.dig.z + 0.5 });
      swing(p);
    }
    if (--e.dig.timer > 0) return;
    breakAt(dim, e.dig.x, e.dig.y, e.dig.z);
    e.dig = undefined;
    return;
  }
  if (e.wait && tick < e.wait) return;
  // out? (we can walk on toward the goal, and it isn't far above us): back to whatever we were doing
  const at = p.location;
  const gd = Math.hypot(e.goal.x - at.x, e.goal.z - at.z);
  if (e.tries > 0 && e.goal.y <= at.y + 2.5 &&
      (gd < 1.5 || groundY(dim, at.x + (e.goal.x - at.x) / gd, at.z + (e.goal.z - at.z) / gd, at.y) !== undefined)) {
    escape = null;
    progress = null;
    last = "climbed out";
    return;
  }
  const f = { x: Math.floor(p.location.x), y: Math.floor(p.location.y + 0.01), z: Math.floor(p.location.z) };
  // which way: toward the goal, else the side with the lowest wall
  const dirs = [[1, 0], [-1, 0], [0, 1], [0, -1]];
  const gx = e.goal.x - (f.x + 0.5);
  const gz = e.goal.z - (f.z + 0.5);
  const wall = ([dx, dz]) => {
    let h = 0;
    while (h < 6 && !soft(blockAt(dim, f.x + dx, f.y + h, f.z + dz))) h++;
    return h;
  };
  dirs.sort((a, b) => wall(a) - wall(b) - 0.5 * ((a[0] * gx + a[1] * gz) - (b[0] * gx + b[1] * gz)) / (Math.hypot(gx, gz) || 1));
  if (Math.hypot(gx, gz) > 0.8) {
    const toward = Math.abs(gx) > Math.abs(gz) ? [Math.sign(gx), 0] : [0, Math.sign(gz)];
    if (wall(toward) <= 5) dirs.unshift(toward);
  }
  const [dx, dz] = dirs[0];
  const nx = f.x + dx;
  const nz = f.z + dz;
  // can we just step up there now?
  if (soft(blockAt(dim, nx, f.y + 1, nz)) && soft(blockAt(dim, nx, f.y + 2, nz)) && soft(blockAt(dim, f.x, f.y + 2, f.z)) &&
      floorOk(blockAt(dim, nx, f.y, nz))) {
    p.teleport({ x: nx + 0.5, y: f.y + 1, z: nz + 0.5 }, { facingLocation: { x: nx + dx + 0.5, y: f.y + 2.6, z: nz + dz + 0.5 } });
    e.tries++;
    e.wait = tick + 4;
    return;
  }
  // otherwise dig the step: head room above us, then the two blocks in front, one up
  for (const [x, y, z] of [[f.x, f.y + 2, f.z], [nx, f.y + 2, nz], [nx, f.y + 1, nz]]) {
    const b = blockAt(dim, x, y, z);
    if (!b || soft(b)) continue;
    if (/bedrock|obsidian|barrier|reinforced/.test(b.typeId) || isNear(dim, b.location, /lava|water/)) break;
    if (protectedAt(dim, x, y, z)) break; // never dig through someone's build: build up instead
    const kind = Object.values(MINE).find((k) => k.m.test(b.typeId)) ?? { tool: "", hand: 20, need: -1 };
    const tier = kind.tool ? hold(p, kind.tool) : undefined;
    if (kind.need >= 0 && (tier === undefined || TIERS.indexOf(tier) > kind.need)) break;
    e.dig = { x, y, z, timer: Math.round(kind.hand / (tier ? SPEEDUP[tier] : 1)) };
    e.tries++;
    return;
  }
  // can't dig our way: build up under ourselves, like jumping and placing a block; but only to get out of a pit
  // (walls higher than a step all round) and never more than the walls are high
  const pit = dirs.every((d) => wall(d) >= 2);
  e.pillar ??= 0;
  const block = Object.keys(inventory(p)).map((k) => "minecraft:" + k).find((id) => PILLAR.test(id));
  if (pit && e.pillar < 6 && block && soft(blockAt(dim, f.x, f.y + 2, f.z)) && !nearPlayerBuild(f.x, f.y, f.z, 2)) {
    e.pillar++;
    take(p, block.replace("minecraft:", ""), 1);
    p.teleport({ x: f.x + 0.5, y: f.y + 1, z: f.z + 0.5 });
    blockAt(dim, f.x, f.y, f.z)?.setType(block);
    OWNED.add(`${f.x},${f.y},${f.z}`);
    e.tries++;
    e.wait = tick + 6;
    return;
  }
  e.tries = 99; // nothing more we can do
}

function faceTo(p, e) {
  const l = e.location ?? e;
  const look = { x: l.x, y: l.y + (e.location ? 1.5 : 0), z: l.z };
  controls(p, { f: 0, y: yawTo(p.location, look), p: pitchTo(p.location, look) });
}

// what a prankster is carrying off, and from whom: dropped at the victim's feet when they catch up
const SNATCHED = {};
let lastShow = -10000;
/** Give another companion a task right now (a reflex, before his brain has caught up). */
function setTask(name, t) {
  const st = STATES.get(name);
  if (!st) return;
  t.until = tick + (t.secs ?? 30) * 20;
  t.n ??= 1;
  t.done = 0;
  t.prev = st.task && st.task.act !== "chase" ? st.task : undefined; // carry on afterwards
  st.task = t;
}
function tell(who, prank) {
  console.warn("[claude] " + JSON.stringify({ who, prank }));
  const st = STATES.get(who);
  if (st) st.last = prank;
}
function caught(p) {
  const list = SNATCHED[p.name];
  if (!list?.length) return;
  for (const s of [...list]) {
    const q = world.getPlayers({ name: s.from })[0];
    if (!q || flat(q.location, p.location) > 1.6) continue;
    if (count(p, short(s.item)) === 0) {
      list.splice(list.indexOf(s), 1);
      continue;
    }
    const n = Math.min(s.n, count(p, short(s.item)));
    take(p, short(s.item), n);
    p.dimension.spawnItem(new ItemStack(s.item, n), q.location);
    list.splice(list.indexOf(s), 1);
    tell(q.name, `you caught ${p.name}! Your ${short(s.item)} dropped at your feet.`);
    out({ result: `${q.name} caught me and got the ${short(s.item)} back, hehe` });
  }
}

// --- every tick --------------------------------------------------------------------------------------

// where real players died lately: their things lie there for them to come back for, so companions leave them alone
const DEATHS = [];
world.afterEvents.entityDie.subscribe((ev) => {
  const e = ev.deadEntity;
  if (e.typeId === "minecraft:player" && e.hasTag("companion")) {
    // a companion's own things, dropped where it died: it may go back and pick them up for a while
    OWN_DEATH.set(e.name, { at: { ...e.location }, tick });
    return;
  }
  if (e.typeId !== "minecraft:player") return;
  DEATHS.push({ at: { ...e.location }, tick, who: e.name });
  if (DEATHS.length > 20) DEATHS.shift();
});
const nearDeath = (l) => DEATHS.some((d) => tick - d.tick < 6000 && dist(d.at, l) < 10);

function pickUp(p) {
  const ps = world.getPlayers().filter((q) => q.name !== p.name); // never grab what's nearer someone else
  for (const e of p.dimension.getEntities({ location: p.location, maxDistance: 2.5, type: "minecraft:item" })) {
    // never take what's nearer to someone else, or what someone dropped when they died: that's theirs
    if (ps.some((q) => dist(q.location, e.location) < dist(p.location, e.location))) continue;
    if (nearDeath(e.location)) continue;
    if (!mine_(p.name, e.location)) continue; // only our own drops: never anyone else's things
    const it = e.getComponent("minecraft:item")?.itemStack;
    if (!it) continue;
    const left = inv(p).addItem(it);
    if (!left) e.remove();
  }
}

function reflexes(p) {
  if (task?.act === "attack") return;
  // without a weapon a companion doesn't pick fights (Rex is unarmed by design): he backs away from danger instead
  if (p.hasTag("pacifist") && count(p, "sword") === 0) { // unarmed: step away; with a sword he made himself: stand and fight
    const e = hostiles(p, p.location, 3.5)[0]; // only when it's really close: otherwise get on with things
    if (e && task?.act !== "run_away") {
      const prev = task;
      start({ act: "run_away", player: "", secs: 4 });
      task.to = { x: p.location.x + (p.location.x - e.location.x) * 2, z: p.location.z + (p.location.z - e.location.z) * 2 };
      task.prev = prev;
    }
    return;
  }
  // a monster going for someone close by: deal with it first, then carry on
  const range = p.hasTag("armoured") || p.hasTag("strong") ? 28 : 16;
  for (const who of [p, ...others().filter((q) => dist(q.location, p.location) < range)]) {
    const e = hostiles(p, who.location, who === p ? 4 : p.hasTag("armoured") ? 10 : 6).find((m) => m.typeId !== "minecraft:creeper" || who === p || p.hasTag("armoured"));
    if (e) {
      const prev = task;
      start({ act: "attack", mob: short(e.typeId), secs: 20, reflex: true });
      task.target = e;
      task.prev = prev;
      out({ reflex: "defend " + who.name + " from " + short(e.typeId) });
      return;
    }
  }
}

function idle(p) {
  // nobody to keep company with ("-"): stay put, just looking at whoever comes close
  if (buddy === "-") {
    const w = others().sort((a, b) => dist(a.location, p.location) - dist(b.location, p.location))[0];
    if (w && tick % 20 === 0 && dist(w.location, p.location) < 12) faceTo(p, w);
    return;
  }
  // a build site to keep to: wait there, watching whoever's about
  if (anchor) {
    const d = flat(anchor, p.location);
    if (d > anchor.r && d < 200) return void step(p, anchor.x + 0.5, anchor.z + 0.5, undefined, d > 12 ? RUN : WALK);
    const w = others().sort((a, b) => dist(a.location, p.location) - dist(b.location, p.location))[0];
    if (w && tick % 20 === 0 && dist(w.location, p.location) < 16) faceTo(p, w);
    return;
  }
  // with nothing to do, stay with your buddy (or else the nearest player) like a friend would
  const q = world.getPlayers({ name: buddy })[0] ??
    others().sort((a, b) => dist(a.location, p.location) - dist(b.location, p.location))[0];
  if (!q) return;
  const d = flat(q.location, p.location);
  if (d > keep && d < 200) step(p, q.location.x, q.location.z, undefined, d > keep + 6 ? RUN : WALK);
  else if (tick % 20 === 0) faceTo(p, q);
}

system.run(() => loadPlaced());
system.runInterval(() => {
  tick++;
  if (placedDirty && tick % 200 === 0) savePlaced();
  for (const c of companions()) as(c.name, () => run(c));
}, 1);

/** Like the game pushing a player out of a block they're stuck in: up to the nearest free spot. */
function unembed(p) {
  const dim = p.dimension;
  const l = p.location;
  const free = soft(blockAt(dim, l.x, l.y + 0.01, l.z)) && soft(blockAt(dim, l.x, l.y + 1.1, l.z));
  // standing in a one-block slot (a gap in a floor): blocks all round at our feet
  const boxed = [[1, 0], [-1, 0], [0, 1], [0, -1]].every(([dx, dz]) => !soft(blockAt(dim, l.x + dx, l.y + 0.01, l.z + dz)));
  if (free && !boxed) return false;
  if (!free) {
    // buried: up to the first open spot above, however deep (the game pushes stuck players out the same way)
    for (let y = Math.floor(l.y) + 1; y < Math.floor(l.y) + 24; y++) {
      if (soft(blockAt(dim, l.x, y, l.z)) && soft(blockAt(dim, l.x, y + 1, l.z)) && floorOk(blockAt(dim, l.x, y - 1, l.z))) {
        p.teleport({ x: l.x, y, z: l.z });
        out({ reflex: "was buried, popped out" });
        return true;
      }
    }
    return false;
  }
  // hop up onto a neighbouring block, like a player jumping out
  for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    const x = Math.floor(l.x) + dx + 0.5;
    const z = Math.floor(l.z) + dz + 0.5;
    const y = groundY(dim, x, z, l.y + 1, 0);
    if (y !== undefined && y <= Math.floor(l.y) + 1) {
      p.teleport({ x, y, z });
      return true;
    }
  }
  return false;
}


/** A champion never gets lost: far from his buddy (40+ blocks) and not getting closer for 15 s, he's at her side. */
const CATCHUP = new Map();
function catchUp(p) {
  if (!p.hasTag("strong") || !buddy) return false;
  const q = world.getPlayers({ name: buddy })[0];
  if (!q) return false;
  const d = dist(q.location, p.location);
  const c = CATCHUP.get(p.name);
  if (d < 40) return void CATCHUP.delete(p.name);
  if (!c || d < c.d - 4) return void CATCHUP.set(p.name, { d, tick });
  if (tick - c.tick < 300) return false;
  CATCHUP.delete(p.name);
  p.teleport(q.location);
  out({ reflex: "caught up with " + q.name });
  return true;
}

function run(p) {
  try {
    if (p.getComponent("minecraft:riding")?.entityRidingOn) {
      // riding (Kostas at the wheel): only the driving task runs; no walking, falling or climbing
      if (task?.act === "taxi" || task?.act === "park") RUNNERS[task.act](p, task);
      return;
    }
    if (tick % 20 === 10 && catchUp(p)) return;
    const crouch = !!task && /^(build|dig|mine)$/.test(task.act);
    if (p.isSneaking !== crouch) p.isSneaking = crouch;
    if (tick % 20 === 0 && unembed(p)) return;
    if (tick % 10 === 0) pickUp(p);
    if (tick % 10 === 5) caught(p);
    if (tick % 2 === 0) survey(p, 300);
    if (tick % 5 === 0 && world.getDifficulty() !== "Peaceful") reflexes(p); // no monsters on peaceful
    if (task && tick > task.until) finish(`gave up on ${describe(task)} (took too long)`);
    if (unstick(p)) return;
    if (task?.walkTo) {
      if (tick < task.walkTo.until && flat(task.walkTo, p.location) > 0.6) return void step(p, task.walkTo.x, task.walkTo.z);
      task.walkTo = undefined;
    }
    if (task) RUNNERS[task.act](p, task);
    else idle(p);
  } catch (e) {
    if (tick - errAt > 100) {
      errAt = tick;
      out({ error: String(e), task: task && describe(task) });
    }
    if (task) finish(`something went wrong with ${describe(task)}`);
  }
}

system.afterEvents.scriptEventReceive.subscribe((ev) => {
  if (!ev.id.startsWith("claude:")) return;
  const [name, ...rest] = ev.message.trim().split(" ");
  as(name, () => handle(ev.id, rest.join(" ")));
});

function handle(id, msg) {
  if (id === "claude:site") {
    // "x0 y0 z0 x1 y1 z1": the build site, where the crew may dig and build; nothing: no site
    const v = msg.trim().split(/\s+/).map(Number);
    site = v.length === 6 && v.every(Number.isFinite) ? v : null;
    return;
  }
  if (id === "claude:anchor") {
    // "x y z" to keep to a place when idle; nothing to go back to following the buddy
    const [x, y, z, r] = msg.trim().split(/\s+/).map(Number);
    anchor = [x, y, z].every(Number.isFinite) && msg.trim() ? { x, y, z, r: r || 5 } : null;
    return;
  }
  if (id === "claude:arm") {
    // a protector's sword: netherite, Sharpness V (the game's highest), Unbreaking III, Mending; kept in hand
    const p = bot();
    if (!p) return;
    p.addTag("armoured");
    const c = inv(p);
    let has = -1;
    for (let i = 0; i < c.size; i++) if (c.getItem(i)?.typeId === "minecraft:netherite_sword") has = i;
    if (has < 0) {
      const sword = new ItemStack("minecraft:netherite_sword", 1);
      sword.nameTag = "Guardian's Blade";
      const ench = sword.getComponent("minecraft:enchantable");
      for (const [type, level] of [["sharpness", 5], ["unbreaking", 3], ["mending", 1]]) {
        try {
          ench?.addEnchantment({ type: new EnchantmentType(type), level });
        } catch (e) {
          out({ error: `enchant ${type}: ${e}` });
        }
      }
      const old = c.getItem(0);
      c.setItem(0, sword);
      if (old) c.addItem(old);
      has = 0;
    }
    if (has <= 8) p.selectedSlotIndex = has;
    return;
  }
  if (id === "claude:hello") {
    // "<who to keep company with when idle: a name, * for whoever's nearest, - for nobody> [how far]"
    const [b, k] = msg.trim().split(/\s+/);
    buddy = !b || b === "*" ? "" : b;
    keep = Number(k) || 6;
    bot()?.addTag("companion");
    return;
  }
  if (id === "claude:look") look(msg);
  if (id === "claude:items") {
    // every dropped item within r of x,z, in piles (6 blocks): where things are lying, and what
    const [x, z, r] = msg.trim().split(/\s+/).map(Number);
    const piles = [];
    for (const e of world.getDimension("overworld").getEntities({ type: "minecraft:item", location: { x, y: 64, z }, maxDistance: r || 300 })) {
      const it = e.getComponent("minecraft:item")?.itemStack;
      if (!it) continue;
      const l = e.location;
      let pile = piles.find((q) => Math.hypot(q.x - l.x, q.y - l.y, q.z - l.z) < 6);
      if (!pile) piles.push((pile = { x: Math.floor(l.x), y: Math.floor(l.y), z: Math.floor(l.z), items: [] }));
      pile.items.push(`${short(it.typeId)}${it.nameTag ? "(" + it.nameTag + ")" : ""} x${it.amount}`);
    }
    out({ piles });
  }
  if (id === "claude:map") {
    // for the live map page (tools/map_server.py): everyone's position and health, nearby monsters, and (when asked)
    // the top block of every other column around the players, as one letter each
    const dim = world.getDimension("overworld");
    const ps = world.getPlayers().map((q) => ({ n: q.name, c: q.hasTag("companion") ? 1 : 0, x: Math.round(q.location.x * 10) / 10,
      y: Math.floor(q.location.y), z: Math.round(q.location.z * 10) / 10, hp: hp(q) }));
    const kids = world.getPlayers().filter((q) => !q.hasTag("companion"));
    const center = kids[0]?.location ?? world.getPlayers()[0]?.location ?? { x: 0, y: 64, z: 0 };
    const mobs = [];
    for (const e of dim.getEntities({ location: center, maxDistance: 64, families: ["monster"] }))
      if (e.typeId !== "minecraft:player") mobs.push([short(e.typeId), Math.round(e.location.x), Math.round(e.location.z)]);
    const o = { map: BOT, t: world.getTimeOfDay(), ps, mobs: mobs.slice(0, 60), cx: Math.floor(center.x), cz: Math.floor(center.z) };
    if (msg.includes("terrain")) {
      const R = 48;
      const S = 2;
      const K = [[/water/, "w"], [/lava/, "l"], [/leaves/, "f"], [/_log$/, "t"], [/sand$/, "s"], [/snow|ice/, "i"], [/grass_block|moss/, "g"],
        [/dirt|farmland|mud|path/, "d"], [/_planks|crafting|chest|door|furnace|glass|bricks|cobblestone|torch|wool|bed/, "b"],
        [/stone|deepslate|andesite|diorite|granite|tuff|gravel|ore/, "r"]];
      let rows = "";
      for (let dz = -R; dz <= R; dz += S) {
        for (let dx = -R; dx <= R; dx += S) {
          let ch = ".";
          try {
            const b = dim.getTopmostBlock({ x: o.cx + dx, z: o.cz + dz });
            if (b) ch = (K.find(([m]) => m.test(b.typeId)) ?? [null, "o"])[1];
          } catch {}
          rows += ch;
        }
        rows += "|";
      }
      o.terrain = { r: R, s: S, rows };
    }
    console.warn("[claude] " + JSON.stringify({ who: "map", ...o }));
    return;
  }
  if (id === "claude:find") {
    // admin: "kind x z r" -> the nearest blocks of that kind in a big box. Spread over many ticks with runJob
    // (a big one-tick scan crashed the server once), every 4th column, heights -60..100.
    const [kind, x, z, r] = msg.trim().split(/\s+/);
    const m = new RegExp("^minecraft:" + kind + "$");
    const dim = world.getDimension("overworld");
    const R = Math.min(Number(r) || 96, 128);
    const found = [];
    system.runJob(
      (function* () {
        for (let ring = 0; ring <= R; ring += 4) {
          for (let dx = -ring; dx <= ring; dx += 4)
            for (let dz = -ring; dz <= ring; dz += 4) {
              if (Math.max(Math.abs(dx), Math.abs(dz)) !== ring) continue;
              for (let y = 100; y >= -60; y--) {
                const b = blockAt(dim, Number(x) + dx, y, Number(z) + dz);
                if (b && m.test(b.typeId)) {
                  found.push([b.location.x, y, b.location.z, Math.round(Math.hypot(dx, dz))]);
                  break;
                }
              }
              yield;
            }
          if (found.length >= 6) break;
        }
        console.warn("[claude] " + JSON.stringify({ who: "find", find: kind, near: found.slice(0, 6) }));
      })(),
    );
  }
  if (id === "claude:deaths") out({ deaths: DEATHS.map((d) => ({ who: d.who, at: [Math.floor(d.at.x), Math.floor(d.at.y), Math.floor(d.at.z)], secondsAgo: Math.round((tick - d.tick) / 20) })) });
  if (id === "claude:survey") {
    // what's been built around a point: "x y z [radius]"
    const [x, y, z, r] = msg.trim().split(/\s+/).map(Number);
    out({ survey: [x, y, z], built: builtNear({ location: { x, y, z }, dimension: world.getDimension("overworld") }, r || 12, 2000) });
  }
  if (id === "claude:debug") out({ debug: task && { ...task, target: undefined, prev: undefined }, escape, buddy, at: bot()?.location, intent: INTENT.get(BOT), tick,
    nav: (() => { const n = NAV.get(BOT); return n && { goal: n.goal, searching: !!n.search, nodes: n.search?.n, len: n.path?.length, i: n.i, next: n.path?.[n.i] }; })(),
    controls: (() => { try { const b = bot(); return { move: b.inputInfo.getMovementVector(), mode: String(b.inputInfo.lastInputModeUsed) }; } catch (e) { return String(e); } })() });
  if (id !== "claude:do") return;
  try {
    const t = JSON.parse(msg);
    if (t.act === "continue") return;
    if (t.act === "idle") {
      task = null;
      return;
    }
    if (ACTIONS[t.act]?.runs_in !== "game" || !RUNNERS[t.act]) return out({ result: "unknown action " + t.act });
    if ((task?.act === "perform" || task?.act === "sing") && tick < task.until) return; // the show goes on
    if (t.count) t.n = Math.max(1, Math.min(64, Number(t.count)));
    t.secs = ACTIONS[t.act].secs;
    start(t);
  } catch (e) {
    out({ result: "bad command " + msg });
  }
}
