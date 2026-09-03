import { cssFont } from "./media.js?v=4";

const BLEND = {
  alpha: "source-over",
  add: "lighter",
  subtract: "difference",
  multiply: "multiply",
  lighten: "lighten",
  darken: "darken",
  screen: "screen",
  replace: "copy",
};

function cssColor(c, opacity) {
  const a = (c[3] ?? 1) * (opacity ?? 1);
  return `rgba(${Math.round(c[0] * 255)}, ${Math.round(c[1] * 255)}, ${Math.round(c[2] * 255)}, ${a})`;
}

function parseTagged(text, defaultColor) {
  const runs = [];
  const stack = [];
  let color = defaultColor;
  let i = 0;
  let buf = "";
  const flush = () => {
    if (!buf) return;
    runs.push({ text: buf, color });
    buf = "";
  };
  while (i < text.length) {
    if (text[i] === "{") {
      const close = text.indexOf("}", i + 1);
      if (close < 0) {
        buf += text[i++];
        continue;
      }
      const inner = text.slice(i + 1, close);
      flush();
      if (inner === "/c" || inner === "/" || inner === "/b" || inner === "/i") {
        color = stack.pop() || defaultColor;
      } else {
        const hex = inner.match(/^c:(#[0-9a-fA-F]{6})$/) || inner.match(/^(#[0-9a-fA-F]{6})$/);
        if (hex) {
          stack.push(color);
          const h = hex[1].slice(1);
          color = [
            parseInt(h.slice(0, 2), 16) / 255,
            parseInt(h.slice(2, 4), 16) / 255,
            parseInt(h.slice(4, 6), 16) / 255,
            1,
          ];
        } else {
          buf += text.slice(i, close + 1);
        }
      }
      i = close + 1;
    } else {
      buf += text[i++];
    }
  }
  flush();
  return runs;
}

function applyReveal(runs, reveal) {
  if (reveal == null || reveal >= 1) return runs;
  if (reveal <= 0) return [];
  const total = runs.reduce((n, run) => n + [...run.text].length, 0);
  let remain = Math.floor(total * reveal + 1e-4);
  const out = [];
  for (const run of runs) {
    if (remain <= 0) break;
    const chars = [...run.text];
    const take = Math.min(chars.length, remain);
    out.push({ text: chars.slice(0, take).join(""), color: run.color });
    remain -= take;
  }
  return out;
}

function wrapRuns(ctx, runs, width) {
  const lines = [[]];
  let x = 0;
  const newline = () => {
    lines.push([]);
    x = 0;
  };
  for (const run of runs) {
    const parts = run.text.split(/(\n| +)/);
    for (const part of parts) {
      if (!part) continue;
      if (part === "\n") {
        newline();
        continue;
      }
      const w = ctx.measureText(part).width;
      if (width && x > 0 && x + w > width) newline();
      lines[lines.length - 1].push({ text: part, color: run.color, w });
      x += w;
    }
  }
  return lines;
}

function roundRect(ctx, x, y, w, h, rx) {
  const r = Math.max(0, Math.min(rx || 0, w / 2, h / 2));
  if (ctx.roundRect) {
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, r);
    return;
  }
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function phash(seed, k) {
  const s = Math.sin(seed * 12.9898 + k * 78.233) * 43758.5453;
  return s - Math.floor(s);
}

function drawParticles(ctx, node, t) {
  const n = node.n || 0;
  const seed = node.seed || 1;
  const life = node.life || 1.2;
  const emit = node.emit || 0;
  const window = node.emit_window || 0.2;
  const spread = node.spread ?? Math.PI;
  const heading = node.heading ?? -Math.PI / 2;
  const speed0 = node.speed || 240;
  const grav = node.gravity || 520;
  const rad = node.r || 3.5;
  for (let i = 1; i <= n; i++) {
    const u1 = phash(seed, i);
    const u2 = phash(seed, i + 97);
    const u3 = phash(seed, i + 193);
    const birth = emit + u1 * window;
    const age = t - birth;
    if (age < 0 || age >= life) continue;
    const ang = heading + (u2 - 0.5) * spread;
    const spd = speed0 * (0.55 + 0.9 * u3);
    const fade = 1 - age / life;
    const px = Math.cos(ang) * spd * age;
    const py = Math.sin(ang) * spd * age + 0.5 * grav * age * age;
    ctx.beginPath();
    ctx.fillStyle = cssColor(node.color, node.opacity * fade);
    ctx.arc(px, py, rad * (0.45 + 0.55 * fade), 0, Math.PI * 2);
    ctx.fill();
  }
}

function drawCmds(ctx, cmds, opacity) {
  for (const cmd of cmds) {
    const color = cssColor(cmd.color, opacity * (cmd.opacity ?? 1));
    if (cmd.kind === "rect") {
      ctx.fillStyle = color;
      roundRect(ctx, cmd.x, cmd.y, cmd.w, cmd.h, cmd.rx || 0);
      ctx.fill();
    } else if (cmd.kind === "arc") {
      ctx.beginPath();
      ctx.moveTo(cmd.x, cmd.y);
      ctx.arc(cmd.x, cmd.y, cmd.r, cmd.a0, cmd.a1);
      ctx.closePath();
      if (cmd.fill) {
        ctx.fillStyle = color;
        ctx.fill();
      } else {
        ctx.strokeStyle = color;
        ctx.stroke();
      }
    } else if (cmd.kind === "polyline") {
      const pts = cmd.pts || [];
      if (pts.length < 4) continue;
      ctx.beginPath();
      ctx.moveTo(pts[0], pts[1]);
      for (let i = 2; i < pts.length; i += 2) ctx.lineTo(pts[i], pts[i + 1]);
      if (cmd.fill) {
        ctx.closePath();
        ctx.fillStyle = color;
        ctx.fill();
      } else {
        ctx.strokeStyle = color;
        ctx.lineWidth = cmd.width || 2;
        ctx.lineJoin = "bevel";
        ctx.stroke();
      }
    }
  }
}

function origin(node, w, h) {
  if (node.anchor === "center") return [-w / 2, -h / 2];
  return [0, 0];
}

function withRoundClip(ctx, x, y, w, h, rx, fn) {
  ctx.save();
  if (rx > 0) {
    roundRect(ctx, x, y, w, h, rx);
    ctx.clip();
  }
  fn();
  ctx.restore();
}

function stretchDraw(ctx, src, x, y, w, h, rx, opacity) {
  if (!src) return;
  ctx.save();
  ctx.globalAlpha = opacity;
  withRoundClip(ctx, x, y, w, h, rx, () => {
    ctx.drawImage(src, x, y, w, h);
  });
  ctx.restore();
}

function coverDraw(ctx, src, x, y, w, h, rx, opacity) {
  if (!src) return;
  const sw = src.videoWidth || src.naturalWidth || src.width;
  const sh = src.videoHeight || src.naturalHeight || src.height;
  if (!sw || !sh) return;
  const scale = Math.max(w / sw, h / sh);
  const cw = w / scale;
  const ch = h / scale;
  const sx = (sw - cw) / 2;
  const sy = (sh - ch) / 2;
  ctx.save();
  ctx.globalAlpha = opacity;
  withRoundClip(ctx, x, y, w, h, rx, () => {
    try {
      ctx.drawImage(src, sx, sy, cw, ch, x, y, w, h);
    } catch (err) {
      console.warn("ellua web: video draw failed", err);
    }
  });
  ctx.restore();
}

function sheetIndex(node, t, frames) {
  const rel = Math.max(0, t - (node.from || 0));
  const n = frames.length;
  if (!n) return 0;
  if (node.fps) {
    let idx = Math.floor(rel * node.fps);
    idx = node.loop === false ? Math.min(idx, n - 1) : idx % n;
    return idx;
  }
  let acc = 0;
  let chosen = n - 1;
  for (let i = 0; i < n; i++) {
    acc += frames[i].duration || 1 / 12;
    if (rel < acc) {
      chosen = i;
      break;
    }
  }
  if (node.loop === false) return Math.min(chosen, n - 1);
  if (rel < acc) return chosen;
  const total = acc;
  if (total <= 0) return 0;
  const r = rel % total;
  acc = 0;
  for (let i = 0; i < n; i++) {
    acc += frames[i].duration || 1 / 12;
    if (r < acc) return i;
  }
  return n - 1;
}

export function measureText(text, size, font) {
  const canvas = measureText._c || (measureText._c = document.createElement("canvas"));
  const ctx = canvas.getContext("2d");
  ctx.font = cssFont(size, font);
  return ctx.measureText(text).width;
}

export function paint(ctx, frame, t, media) {
  const { width, height, bg, nodes } = frame;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  ctx.fillStyle = cssColor(bg, 1);
  ctx.fillRect(0, 0, width, height);

  for (const node of nodes) {
    ctx.save();
    ctx.setTransform(node.a, node.b, node.c, node.d, node.e, node.f);
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = BLEND[node.blend] || "source-over";

    if (node.kind === "rect" || node.kind === "surface") {
      const [x, y] = origin(node, node.w, node.h);
      if (node.shadow) {
        ctx.save();
        ctx.shadowColor = `rgba(0,0,0,${node.shadow.alpha * node.opacity})`;
        ctx.shadowBlur = node.shadow.blur * 0.35;
        ctx.shadowOffsetY = node.shadow.dy;
        ctx.fillStyle = cssColor(node.color, node.opacity);
        roundRect(ctx, x, y, node.w, node.h, node.rx);
        ctx.fill();
        ctx.restore();
      } else {
        ctx.fillStyle = cssColor(node.color, node.opacity);
        roundRect(ctx, x, y, node.w, node.h, node.rx);
        ctx.fill();
      }
    } else if (node.kind === "circle") {
      ctx.beginPath();
      ctx.fillStyle = cssColor(node.color, node.opacity);
      ctx.arc(0, 0, node.r, 0, Math.PI * 2);
      ctx.fill();
    } else if (node.kind === "text") {
      const size = node.size || 32;
      ctx.font = cssFont(size, node.font);
      ctx.textBaseline = "top";
      const tagged = (node.text || "").includes("{");
      let runs = tagged
        ? parseTagged(node.text || "", node.color)
        : [{ text: node.text || "", color: node.color }];
      runs = applyReveal(runs, node.reveal);
      if (node.wrap) {
        const lines = wrapRuns(ctx, runs, node.wrap);
        const lh = size * (node.leading || 1.15);
        let tw = 0;
        let th = lines.length * lh;
        for (const line of lines) {
          const lw = line.reduce((n, run) => n + run.w, 0);
          if (lw > tw) tw = lw;
        }
        const [ox, oy] = origin(node, tw, th);
        lines.forEach((line, li) => {
          let x = ox;
          for (const run of line) {
            ctx.fillStyle = cssColor(run.color, node.opacity);
            ctx.fillText(run.text, x, oy + li * lh);
            x += run.w;
          }
        });
      } else {
        const text = runs.map((run) => run.text).join("");
        const tw = ctx.measureText(text).width;
        const [ox, oy] = origin(node, tw, size);
        let x = ox;
        for (const run of runs) {
          ctx.fillStyle = cssColor(run.color, node.opacity);
          ctx.fillText(run.text, x, oy);
          x += ctx.measureText(run.text).width;
        }
      }
    } else if (node.kind === "particles") {
      drawParticles(ctx, node, t);
    } else if (node.kind === "chart") {
      drawCmds(ctx, node.cmds || [], node.opacity);
    } else if (node.kind === "ornament") {
      ctx.strokeStyle = cssColor(node.color, node.opacity);
      ctx.lineWidth = node.width || 2.5;
      ctx.lineJoin = "bevel";
      ctx.lineCap = "round";
      for (const pts of node.polylines || []) {
        if (!pts || pts.length < 4) continue;
        ctx.beginPath();
        ctx.moveTo(pts[0], pts[1]);
        for (let i = 2; i < pts.length; i += 2) ctx.lineTo(pts[i], pts[i + 1]);
        ctx.stroke();
      }
    } else if (node.kind === "spine") {
      ctx.strokeStyle = cssColor(node.color, node.opacity);
      ctx.fillStyle = cssColor(node.color, node.opacity);
      ctx.lineWidth = 8;
      ctx.lineCap = "round";
      for (const b of node.bones || []) {
        if (b.length > 2) {
          ctx.beginPath();
          ctx.moveTo(b.x, b.y);
          ctx.lineTo(b.x2, b.y2);
          ctx.stroke();
        }
        ctx.beginPath();
        ctx.arc(b.x, b.y, 6, 0, Math.PI * 2);
        ctx.fill();
      }
    } else if (node.kind === "image" || node.kind === "svg") {
      const [x, y] = origin(node, node.w, node.h);
      stretchDraw(ctx, media?.image(node.src), x, y, node.w, node.h, node.rx, node.opacity);
    } else if (node.kind === "video") {
      const el = media?.video(node, t);
      if (node.opacity > 0.001) {
        const [x, y] = origin(node, node.w, node.h);
        coverDraw(ctx, el, x, y, node.w, node.h, node.rx, node.opacity);
      }
    } else if (node.kind === "spritesheet") {
      const sheet = media?.sheet(node.src);
      const img = sheet && media.image(sheet.image);
      if (sheet && img) {
        const frames = sheet.frames;
        const fr = frames[sheetIndex(node, t, frames)];
        const dw = node.w || fr.w;
        const dh = node.h || fr.h;
        const [x, y] = origin(node, dw, dh);
        ctx.save();
        ctx.globalAlpha = node.opacity;
        ctx.drawImage(img, fr.x, fr.y, fr.w, fr.h, x, y, dw, dh);
        ctx.restore();
      }
    } else if (node.kind === "lottie") {
      const canvas = media?.lottieCanvas(node, t);
      if (canvas) {
        const [x, y] = origin(node, node.w, node.h);
        stretchDraw(ctx, canvas, x, y, node.w, node.h, node.rx, node.opacity);
      }
    }

    ctx.restore();
  }
}
