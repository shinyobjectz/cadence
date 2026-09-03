// vello_cpu spike: validate (1) API shape for a rust-host painter,
// (2) hash-exact determinism (render twice, byte-compare),
// (3) CPU throughput at 1080×1920 for an animated multi-shape scene.

use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::time::Instant;
use vello_cpu::color::{palette::css, AlphaColor, Srgb};
use vello_cpu::kurbo::{Affine, BezPath, Circle, Point, Rect};
use vello_cpu::{Pixmap, RenderContext, Resources};

const W: u16 = 1080;
const H: u16 = 1920;

fn star(cx: f64, cy: f64, r: f64, rot: f64) -> BezPath {
    let mut p = BezPath::new();
    for i in 0..10 {
        let ang = rot + i as f64 * std::f64::consts::PI / 5.0;
        let rr = if i % 2 == 0 { r } else { r * 0.45 };
        let pt = Point::new(cx + ang.cos() * rr, cy + ang.sin() * rr);
        if i == 0 { p.move_to(pt) } else { p.line_to(pt) }
    }
    p.close_path();
    p
}

fn draw_scene(ctx: &mut RenderContext, t: f64) {
    ctx.set_paint(AlphaColor::<Srgb>::new([0.08, 0.09, 0.12, 1.0]));
    ctx.fill_rect(&Rect::new(0.0, 0.0, W as f64, H as f64));

    // grid of animated rects
    for i in 0..12 {
        for j in 0..20 {
            let x = 40.0 + i as f64 * 84.0;
            let y = 60.0 + j as f64 * 92.0 + (t * 6.28 + i as f64).sin() * 12.0;
            let hue = (i * 20 + j * 12) as f32 / 255.0;
            ctx.set_paint(AlphaColor::<Srgb>::new([hue, 0.5, 1.0 - hue, 0.9]));
            ctx.fill_rect(&Rect::new(x, y, x + 60.0, y + 60.0));
        }
    }
    // orbiting circles
    for k in 0..30 {
        let ang = t * 2.0 + k as f64 * 0.21;
        use vello_cpu::kurbo::Shape;
        let c = Circle::new(
            Point::new(540.0 + ang.cos() * 380.0, 960.0 + ang.sin() * 700.0),
            26.0,
        );
        ctx.set_paint(css::ORANGE);
        ctx.fill_path(&c.to_path(0.1));
    }
    // big rotating star path
    ctx.set_paint(AlphaColor::<Srgb>::new([1.0, 1.0, 1.0, 0.85]));
    ctx.fill_path(&star(540.0, 960.0, 300.0, t));
    let _ = Affine::IDENTITY; // (transforms available; not exercised further here)
}

fn render_once(t: f64) -> Pixmap {
    let mut ctx = RenderContext::new(W, H);
    let mut res = Resources::new();
    let mut pm = Pixmap::new(W, H);
    draw_scene(&mut ctx, t);
    ctx.flush();
    ctx.render(&mut pm, &mut res);
    pm
}

fn hash_of(pm: &Pixmap) -> u64 {
    let mut h = DefaultHasher::new();
    bytemuck_cast(pm.data()).hash(&mut h);
    h.finish()
}

fn main() {
    // determinism: same t rendered twice must be byte-identical
    let a = render_once(0.37);
    let b = render_once(0.37);
    let (ha, hb) = (hash_of(&a), hash_of(&b));
    println!("DETERMINISM {} (a={ha:016x} b={hb:016x})", if ha == hb { "PASS" } else { "FAIL" });

    // dump a frame for eyeball check (raw RGBA premultiplied)
    std::fs::write("/tmp/vello_frame.raw", bytemuck_cast(a.data())).unwrap();

    // throughput: 90 animated frames
    let start = Instant::now();
    let n = 90;
    let mut acc = 0u64;
    for i in 0..n {
        let pm = render_once(i as f64 / 30.0);
        acc ^= pm.data()[0].r as u64; // defeat dead-code elim
    }
    let dt = start.elapsed().as_secs_f64();
    println!("THROUGHPUT {n} frames in {dt:.2}s = {:.1} fps at {W}x{H} (acc={acc})", n as f64 / dt);
}

fn bytemuck_cast(data: &[vello_cpu::color::PremulRgba8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(data.len() * 4);
    for px in data {
        out.extend_from_slice(&[px.r, px.g, px.b, px.a]);
    }
    out
}
