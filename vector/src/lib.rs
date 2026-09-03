// ellua-vector: display-list → vello_cpu → premultiplied RGBA buffer.
// The love host builds a flat f32 command stream per frame (pure f(t) in Lua),
// this renders it. Gives ellua antialiased paths/beziers/gradients that
// love.graphics lacks, with hash-exact CPU rendering.
//
// Command stream (f32s):
//   0  rect        x y w h  r g b a
//   1  rrect       x y w h rad  r g b a
//   2  circle      cx cy rad  r g b a
//   3  move_to     x y
//   4  line_to     x y
//   5  cubic_to    x1 y1 x2 y2 x y
//   6  fill_close  r g b a           (closes+fills current path)
//   7  stroke      width  r g b a    (strokes current path, no close)
//   8  grad_rect   x y w h  x0 y0 x1 y1  r0 g0 b0 a0  r1 g1 b1 a1
//   9  radial      cx cy rad  r0 g0 b0 a0  r1 g1 b1 a1   (blob: center -> edge)
//   10 grain       amount seed                (post-pass film grain over buffer)
//
// C ABI: el_vec_render(cmds, len, w, h, out, out_len) -> 0 | -1

use std::os::raw::c_int;
use std::panic::{catch_unwind, AssertUnwindSafe};
use vello_cpu::color::{AlphaColor, DynamicColor, Srgb};
use vello_cpu::kurbo::{BezPath, Circle, Point, Rect, RoundedRect, Shape, Stroke};
use vello_cpu::peniko::{ColorStop, Gradient};
use vello_cpu::{Pixmap, RenderContext, Resources};

struct Reader<'a> {
    d: &'a [f32],
    i: usize,
}

impl<'a> Reader<'a> {
    fn next(&mut self) -> Option<f32> {
        let v = self.d.get(self.i).copied();
        self.i += 1;
        v
    }
    fn take<const N: usize>(&mut self) -> Option<[f32; N]> {
        let mut out = [0f32; N];
        for slot in out.iter_mut() {
            *slot = self.next()?;
        }
        Some(out)
    }
}

fn color(r: f32, g: f32, b: f32, a: f32) -> AlphaColor<Srgb> {
    AlphaColor::<Srgb>::new([r, g, b, a])
}

fn run(cmds: &[f32], w: u16, h: u16, out: &mut [u8]) -> Option<()> {
    let mut ctx = RenderContext::new(w, h);
    let mut path = BezPath::new();
    let mut grain: Option<(f32, u32)> = None;
    let mut rd = Reader { d: cmds, i: 0 };

    while let Some(op) = rd.next() {
        match op as u32 {
            0 => {
                let [x, y, rw, rh, r, g, b, a] = rd.take::<8>()?;
                ctx.set_paint(color(r, g, b, a));
                ctx.fill_rect(&Rect::new(x as f64, y as f64, (x + rw) as f64, (y + rh) as f64));
            }
            1 => {
                let [x, y, rw, rh, rad, r, g, b, a] = rd.take::<9>()?;
                ctx.set_paint(color(r, g, b, a));
                let rr = RoundedRect::new(x as f64, y as f64, (x + rw) as f64, (y + rh) as f64, rad as f64);
                ctx.fill_path(&rr.to_path(0.1));
            }
            2 => {
                let [cx, cy, rad, r, g, b, a] = rd.take::<7>()?;
                ctx.set_paint(color(r, g, b, a));
                ctx.fill_path(&Circle::new(Point::new(cx as f64, cy as f64), rad as f64).to_path(0.1));
            }
            3 => {
                let [x, y] = rd.take::<2>()?;
                path.move_to(Point::new(x as f64, y as f64));
            }
            4 => {
                let [x, y] = rd.take::<2>()?;
                path.line_to(Point::new(x as f64, y as f64));
            }
            5 => {
                let [x1, y1, x2, y2, x, y] = rd.take::<6>()?;
                path.curve_to(
                    Point::new(x1 as f64, y1 as f64),
                    Point::new(x2 as f64, y2 as f64),
                    Point::new(x as f64, y as f64),
                );
            }
            6 => {
                let [r, g, b, a] = rd.take::<4>()?;
                path.close_path();
                ctx.set_paint(color(r, g, b, a));
                ctx.fill_path(&path);
                path = BezPath::new();
            }
            7 => {
                let [width, r, g, b, a] = rd.take::<5>()?;
                ctx.set_paint(color(r, g, b, a));
                ctx.set_stroke(Stroke::new(width as f64));
                ctx.stroke_path(&path);
                path = BezPath::new();
            }
            8 => {
                let [x, y, rw, rh, x0, y0, x1, y1] = rd.take::<8>()?;
                let [r0, g0, b0, a0, r1, g1, b1, a1] = rd.take::<8>()?;
                let grad = Gradient::new_linear(
                    Point::new(x0 as f64, y0 as f64),
                    Point::new(x1 as f64, y1 as f64),
                )
                .with_stops([
                    ColorStop { offset: 0.0, color: DynamicColor::from_alpha_color(color(r0, g0, b0, a0)) },
                    ColorStop { offset: 1.0, color: DynamicColor::from_alpha_color(color(r1, g1, b1, a1)) },
                ]);
                ctx.set_paint(grad);
                ctx.fill_rect(&Rect::new(x as f64, y as f64, (x + rw) as f64, (y + rh) as f64));
            }
            9 => {
                let [cx, cy, rad] = rd.take::<3>()?;
                let [r0, g0, b0, a0, r1, g1, b1, a1] = rd.take::<8>()?;
                let grad = Gradient::new_radial(Point::new(cx as f64, cy as f64), rad)
                    .with_stops([
                        ColorStop { offset: 0.0, color: DynamicColor::from_alpha_color(color(r0, g0, b0, a0)) },
                        ColorStop { offset: 1.0, color: DynamicColor::from_alpha_color(color(r1, g1, b1, a1)) },
                    ]);
                ctx.set_paint(grad);
                ctx.fill_rect(&Rect::new(
                    (cx - rad) as f64, (cy - rad) as f64,
                    (cx + rad) as f64, (cy + rad) as f64,
                ));
            }
            10 => {
                let [amount, seed] = rd.take::<2>()?;
                grain = Some((amount, seed as u32));
            }
            _ => return None,
        }
    }

    let mut res = Resources::new();
    let mut pm = Pixmap::new(w, h);
    ctx.flush();
    ctx.render(&mut pm, &mut res);

    let data = pm.data();
    if out.len() != data.len() * 4 {
        return None;
    }
    for (i, px) in data.iter().enumerate() {
        out[i * 4] = px.r;
        out[i * 4 + 1] = px.g;
        out[i * 4 + 2] = px.b;
        out[i * 4 + 3] = px.a;
    }

    // deterministic film-grain post pass (hash noise, seedable for animation)
    if let Some((amount, seed)) = grain {
        let amp = (amount * 255.0) as i32;
        if amp > 0 {
            for i in 0..(w as usize * h as usize) {
                let mut n = (i as u32).wrapping_mul(0x9E3779B9).wrapping_add(seed.wrapping_mul(0x85EBCA6B));
                n ^= n >> 16;
                n = n.wrapping_mul(0x7FEB352D);
                n ^= n >> 15;
                let noise = (n & 0xFF) as i32 - 128;
                let d = noise * amp / 128;
                for c in 0..3 {
                    let v = out[i * 4 + c] as i32 + d;
                    out[i * 4 + c] = v.clamp(0, 255) as u8;
                }
            }
        }
    }
    Some(())
}

#[no_mangle]
pub extern "C" fn el_vec_render(
    cmds: *const f32,
    len: usize,
    w: u16,
    h: u16,
    out: *mut u8,
    out_len: usize,
) -> c_int {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let cmds = unsafe { std::slice::from_raw_parts(cmds, len) };
        let out = unsafe { std::slice::from_raw_parts_mut(out, out_len) };
        run(cmds, w, h, out)
    }));
    match result {
        Ok(Some(())) => 0,
        _ => -1,
    }
}
