// cadence-scene: the single rasterizer under the Lua timeline.
//
// The love host evaluates the scene graph at t and streams the *whole* visible
// tree here as one flat f32 command list (+ a string table). vello_cpu paints it
// into one premultiplied RGBA buffer: one antialiaser, one font stack, one gamma.
// Nothing here depends on previous frames; thread count is pinned by the caller
// because multithreaded tiling changes rounding (±1) in a handful of pixels.
//
// Command stream (f32s):
//   0..10  identical to ellua-vector (rect rrect circle move line cubic fill stroke grad radial grain)
//   100 transform  a b c d e f            absolute affine for subsequent ops
//   102 clear      r g b a                  fill the whole frame (background)
//   103 image      id x y w h rx alpha      registered image scaled into the box
//   104 clip_push  x y w h rx               clip layer (node-local rect) until 105
//   105 clip_pop
//   106 grain      amount seed x y w h      film grain over a frame-space rect
//   107 blend_push mix compose              blend layer until 105 (mix: 0 normal 1 multiply 2 screen 4 darken 5 lighten; compose: 0 srcover 1 plus)
//   108 filter_push kind amount             CSS filter layer until 105 (0 blur 1 brightness 2 contrast 3 saturate 4 grayscale 5 sepia 6 invert 7 opacity 8 hue)
//   110 opacity_push a                      group opacity layer until 105
//   111 fx_push blur bright contrast sat gray sepia invert opacity hue  bx by bw bh
//                                          render until 105 into a scratch frame the size of the
//                                          node box (transformed by the current affine), run
//                                          ellua-effects on it, composite back. Edge clamping
//                                          therefore matches love's per-node buffers.
//   101 text       font size x y ls wrap leading align outline_w  or og ob oa  embolden  nruns
//                  then nruns × (str_off str_len r g b a bold italic)
//                  (x,y) = top-left of the line box, like love.graphics.print
//
// C ABI:
//   cs_font_load(path) -> font id | -1            (id 0 = bundled NotoSans, loaded lazily)
//   cs_text_measure(font, size, text, ls, wrap, leading, out[2]) -> 0 | -1
//   cs_render(cmds, len, strings, strings_len, w, h, threads, out, out_len) -> 0 | -1

use std::collections::HashMap;
use std::ffi::CStr;
use std::os::raw::{c_char, c_int};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::{Arc, Mutex, OnceLock};

use parley::fontique::{Blob, CollectionOptions, FontInfoOverride};
use parley::{
    Alignment, AlignmentOptions, FontContext, FontFamily, FontStyle, FontWeight, Layout,
    LayoutContext, LineHeight, PositionedLayoutItem, StyleProperty,
};
use vello_cpu::color::{AlphaColor, DynamicColor, Srgb};
use vello_cpu::kurbo::{Affine, BezPath, Circle, Point, Rect, RoundedRect, Shape, Stroke};
use vello_cpu::peniko::{BlendMode, Compose, Mix, Brush as PBrush, ColorStop, Extend, FontData, Gradient, ImageBrush, ImageQuality, ImageSampler};
use vello_cpu::{ImageSource, Image};
use vello_cpu::color::PremulRgba8;
use vello_common::paint::ImageId;
use vello_common::filter_effects::{Filter, FilterFunction};
use vello_cpu::{Level, Pixmap, RenderContext, RenderSettings, Resources};

const DEFAULT_FONT: &[u8] = include_bytes!("../fonts/NotoSans-Regular.ttf");

#[derive(Clone, PartialEq, Default, Debug)]
struct Brush([f32; 4]);

struct State {
    fcx: FontContext,
    lcx: LayoutContext<Brush>,
    families: Vec<String>,
    by_path: HashMap<String, i32>,
    ctx: Option<(u16, u16, u16, RenderContext)>,
    res: Resources,
    layout: Layout<Brush>,
    cache: HashMap<String, Layout<Brush>>,
    images: Vec<(ImageId, u16, u16)>,
}

fn state() -> &'static Mutex<State> {
    static S: OnceLock<Mutex<State>> = OnceLock::new();
    S.get_or_init(|| {
        let mut fcx = FontContext {
            collection: parley::fontique::Collection::new(CollectionOptions {
                shared: false,
                system_fonts: false,
                ..Default::default()
            }),
            source_cache: Default::default(),
        };
        let mut st = State {
            lcx: LayoutContext::new(),
            families: Vec::new(),
            by_path: HashMap::new(),
            ctx: None,
            res: Resources::new(),
            layout: Layout::new(),
            cache: HashMap::new(),
            images: Vec::new(),
            fcx: FontContext::new(),
        };
        std::mem::swap(&mut st.fcx, &mut fcx);
        register(&mut st, Arc::new(DEFAULT_FONT.to_vec()));
        Mutex::new(st)
    })
}

fn register(st: &mut State, bytes: Arc<Vec<u8>>) -> i32 {
    let id = st.families.len() as i32;
    let name = format!("cadence-font-{id}");
    let blob = Blob::new(bytes);
    let fams = st.fcx.collection.register_fonts(
        blob,
        Some(FontInfoOverride { family_name: Some(&name), ..Default::default() }),
    );
    if fams.is_empty() {
        return -1;
    }
    st.families.push(name);
    id
}

fn color(r: f32, g: f32, b: f32, a: f32) -> AlphaColor<Srgb> {
    AlphaColor::<Srgb>::new([r, g, b, a])
}

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
        for s in out.iter_mut() {
            *s = self.next()?;
        }
        Some(out)
    }
}

struct TextRun<'a> {
    text: &'a str,
    color: [f32; 4],
    bold: bool,
    italic: bool,
}

struct TextSpec<'a> {
    font: usize,
    size: f32,
    ls: f32,
    wrap: f32,
    leading: f32,
    align: i32,
    runs: Vec<TextRun<'a>>,
}

fn spec_key(spec: &TextSpec) -> String {
    let mut k = format!("{}|{}|{}|{}|{}|{}", spec.font, spec.size, spec.ls, spec.wrap, spec.leading, spec.align);
    for r in &spec.runs {
        k.push('\u{1}');
        k.push_str(r.text);
        k.push_str(&format!("|{:?}|{}|{}", r.color, r.bold, r.italic));
    }
    k
}

// Shaping + line breaking is the expensive half of text; comps re-draw the same
// strings every frame, so keep built layouts (bounded) and swap them in.
fn build_layout(st: &mut State, spec: &TextSpec) {
    let key = spec_key(spec);
    if let Some(l) = st.cache.get(&key) {
        st.layout = l.clone();
        return;
    }
    build_layout_uncached(st, spec);
    if st.cache.len() > 4096 {
        st.cache.clear();
    }
    st.cache.insert(key, st.layout.clone());
}

fn build_layout_uncached(st: &mut State, spec: &TextSpec) {
    let State { fcx, lcx, families, layout, .. } = st;
    let mut text = String::new();
    let mut ranges = Vec::with_capacity(spec.runs.len());
    for r in &spec.runs {
        let s = text.len();
        text.push_str(r.text);
        ranges.push(s..text.len());
    }
    let family = families.get(spec.font).cloned().unwrap_or_else(|| families[0].clone());
    let mut b = lcx.ranged_builder(fcx, &text, 1.0, false);
    b.push_default(FontFamily::named(&family));
    b.push_default(StyleProperty::FontSize(spec.size));
    b.push_default(StyleProperty::LetterSpacing(spec.ls));
    b.push_default(StyleProperty::LineHeight(if spec.leading > 0.0 {
        LineHeight::MetricsRelative(spec.leading)
    } else {
        LineHeight::MetricsRelative(1.0)
    }));
    b.push_default(StyleProperty::Brush(Brush([1.0, 1.0, 1.0, 1.0])));
    for (r, range) in spec.runs.iter().zip(ranges) {
        b.push(StyleProperty::Brush(Brush(r.color)), range.clone());
        if r.bold {
            b.push(StyleProperty::FontWeight(FontWeight::BOLD), range.clone());
        }
        if r.italic {
            b.push(StyleProperty::FontStyle(FontStyle::Italic), range.clone());
        }
    }
    b.build_into(layout, &text);
    layout.break_all_lines(if spec.wrap > 0.0 { Some(spec.wrap) } else { None });
    let al = match spec.align {
        1 => Alignment::Center,
        2 => Alignment::End,
        _ => Alignment::Start,
    };
    layout.align(al, AlignmentOptions::default());
}

fn draw_text(
    st: &mut State,
    ctx: &mut RenderContext,
    base: Affine,
    x: f32,
    y: f32,
    outline: (f32, [f32; 4]),
    embolden: f32,
) {
    let State { res, layout, .. } = st;
    let xf = base * Affine::translate((x as f64, y as f64));
    ctx.set_transform(xf);
    for line in layout.lines() {
        for item in line.items() {
            let PositionedLayoutItem::GlyphRun(gr) = item else { continue };
            let run = gr.run();
            let font: &FontData = run.font();
            let size = run.font_size();
            let skew = run
                .synthesis()
                .skew()
                .map(|a| Affine::skew((a.to_radians()).tan() as f64, 0.0))
                .unwrap_or_default();
            let emb = if run.synthesis().embolden() { size * 0.03 } else { 0.0 } + embolden;
            let glyphs = gr
                .positioned_glyphs()
                .map(|g| vello_cpu::Glyph { id: g.id, x: g.x, y: g.y })
                .collect::<Vec<_>>();
            if outline.0 > 0.0 {
                let c = outline.1;
                ctx.set_paint(color(c[0], c[1], c[2], c[3]));
                ctx.set_stroke(Stroke::new(outline.0 as f64));
                ctx.glyph_run(res, font)
                    .font_size(size)
                    .hint(false)
                    .normalized_coords(run.normalized_coords())
                    .glyph_transform(skew)
                    .stroke_glyphs(glyphs.iter().copied());
            }
            let c = gr.style().brush.0;
            ctx.set_paint(color(c[0], c[1], c[2], c[3]));
            let mut b = ctx
                .glyph_run(res, font)
                .font_size(size)
                .hint(true)
                .normalized_coords(run.normalized_coords())
                .glyph_transform(skew);
            if emb > 0.0 {
                b = b.font_embolden(glifo::FontEmbolden::new(
                    vello_cpu::kurbo::Diagonal2::new(emb as f64, emb as f64 * 0.8),
                ));
            }
            b.fill_glyphs(glyphs.iter().copied());
        }
    }
    ctx.set_transform(base);
}

fn run(st: &mut State, cmds: &[f32], strings: &[u8], w: u16, h: u16, threads: u16, out: &mut [u8]) -> Option<()> {
    if out.len() != w as usize * h as usize * 4 {
        return None;
    }
    // vello_cpu filters are single-threaded only: any 108 in the stream pins threads to 0
    let has_filter = {
        let mut r = Reader { d: cmds, i: 0 };
        let mut found = false;
        while let Some(op) = r.next() {
            let n = match op as u32 { 0 => 8, 1 => 9, 2 => 7, 3 | 4 => 2, 5 => 6, 6 => 4, 7 => 5, 8 => 16, 9 => 11, 10 => 2,
                100 => 6, 101 => { let hdr = r.take::<15>(); match hdr { Some(h) => (h[14] as usize) * 8, None => 0 } }, 102 => 4, 103 => 7, 104 => 5, 105 => 0,
                106 => 6, 107 => 2, 108 => { found = true; 2 }, 110 => 1, 111 => 13, _ => 0 };
            r.i += n;
        }
        found
    };
    let threads = if has_filter { 0 } else { threads };
    let need_new = match &st.ctx {
        Some((cw, ch, ct, _)) => *cw != w || *ch != h || *ct != threads,
        None => true,
    };
    if need_new {
        let settings = RenderSettings {
            level: Level::try_detect().unwrap_or(Level::baseline()),
            num_threads: threads,
        };
        st.ctx = Some((w, h, threads, RenderContext::new_with(w, h, settings)));
    }
    let mut ctx = st.ctx.take().unwrap().3;
    ctx.reset();
    let settings = RenderSettings {
        level: Level::try_detect().unwrap_or(Level::baseline()),
        num_threads: threads,
    };
    // scratch contexts for 111 fx_push; `layers` says whether a 105 pops a
    // vello layer (false) or closes a scratch frame (true)
    let mut subs: Vec<RenderContext> = Vec::new();
    let mut fx_params: Vec<([f32; 9], (i32, i32, u16, u16))> = Vec::new();
    let mut offs: Vec<Affine> = Vec::new(); // scratch-frame offsets, parallel to `subs`
    let mut layers: Vec<bool> = Vec::new();
    let mut temp_images: Vec<ImageId> = Vec::new();
    let mut base = Affine::IDENTITY;
    let mut path = BezPath::new();
    let mut grain: Option<(f32, u32)> = None;
    let mut grains: Vec<(f32, u32, usize, usize, usize, usize)> = Vec::new();
    let mut rd = Reader { d: cmds, i: 0 };
    let sref = |off: f32, len: f32| -> Option<&str> {
        let (o, l) = (off as usize, len as usize);
        std::str::from_utf8(strings.get(o..o + l)?).ok()
    };
    let ok = (|| -> Option<()> {
        while let Some(op) = rd.next() {
            let top: &mut RenderContext = if let Some(s) = subs.last_mut() { s } else { &mut ctx };
            let off = offs.last().copied().unwrap_or(Affine::IDENTITY);
            match op as u32 {
                0 => {
                    let [x, y, rw, rh, r, g, b, a] = rd.take::<8>()?;
                    top.set_paint(color(r, g, b, a));
                    top.fill_rect(&Rect::new(x as f64, y as f64, (x + rw) as f64, (y + rh) as f64));
                }
                1 => {
                    let [x, y, rw, rh, rad, r, g, b, a] = rd.take::<9>()?;
                    top.set_paint(color(r, g, b, a));
                    let rr = RoundedRect::new(x as f64, y as f64, (x + rw) as f64, (y + rh) as f64, rad as f64);
                    top.fill_path(&rr.to_path(0.1));
                }
                2 => {
                    let [cx, cy, rad, r, g, b, a] = rd.take::<7>()?;
                    top.set_paint(color(r, g, b, a));
                    top.fill_path(&Circle::new(Point::new(cx as f64, cy as f64), rad as f64).to_path(0.1));
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
                    path.curve_to(Point::new(x1 as f64, y1 as f64), Point::new(x2 as f64, y2 as f64), Point::new(x as f64, y as f64));
                }
                6 => {
                    let [r, g, b, a] = rd.take::<4>()?;
                    path.close_path();
                    top.set_paint(color(r, g, b, a));
                    top.fill_path(&path);
                    path = BezPath::new();
                }
                7 => {
                    let [width, r, g, b, a] = rd.take::<5>()?;
                    top.set_paint(color(r, g, b, a));
                    top.set_stroke(Stroke::new(width as f64));
                    top.stroke_path(&path);
                    path = BezPath::new();
                }
                8 => {
                    let [x, y, rw, rh, x0, y0, x1, y1] = rd.take::<8>()?;
                    let [r0, g0, b0, a0, r1, g1, b1, a1] = rd.take::<8>()?;
                    let grad = Gradient::new_linear(Point::new(x0 as f64, y0 as f64), Point::new(x1 as f64, y1 as f64)).with_stops([
                        ColorStop { offset: 0.0, color: DynamicColor::from_alpha_color(color(r0, g0, b0, a0)) },
                        ColorStop { offset: 1.0, color: DynamicColor::from_alpha_color(color(r1, g1, b1, a1)) },
                    ]);
                    top.set_paint(grad);
                    top.fill_rect(&Rect::new(x as f64, y as f64, (x + rw) as f64, (y + rh) as f64));
                }
                9 => {
                    let [cx, cy, rad] = rd.take::<3>()?;
                    let [r0, g0, b0, a0, r1, g1, b1, a1] = rd.take::<8>()?;
                    let grad = Gradient::new_radial(Point::new(cx as f64, cy as f64), rad).with_stops([
                        ColorStop { offset: 0.0, color: DynamicColor::from_alpha_color(color(r0, g0, b0, a0)) },
                        ColorStop { offset: 1.0, color: DynamicColor::from_alpha_color(color(r1, g1, b1, a1)) },
                    ]);
                    top.set_paint(grad);
                    top.fill_rect(&Rect::new((cx - rad) as f64, (cy - rad) as f64, (cx + rad) as f64, (cy + rad) as f64));
                }
                10 => {
                    let [amount, seed] = rd.take::<2>()?;
                    grain = Some((amount, seed as u32));
                }
                103 => {
                    let [id, x, y, rw, rh, rx, alpha] = rd.take::<7>()?;
                    let (iid, iw, ih) = *st.images.get(id as usize)?;
                    // vello_cpu 0.2 has no sampler alpha on images: opacity goes through a layer
                    let img: Image = ImageBrush {
                        image: ImageSource::opaque_id(iid),
                        sampler: ImageSampler { x_extend: Extend::Pad, y_extend: Extend::Pad, quality: ImageQuality::Medium, alpha: 1.0 },
                    };
                    let layered = alpha < 0.999;
                    if layered { top.push_opacity_layer(alpha.max(0.0)); }
                    top.set_paint(PBrush::Image(img));
                    top.set_paint_transform(Affine::translate((x as f64, y as f64)) * Affine::scale_non_uniform(rw as f64 / iw as f64, rh as f64 / ih as f64));
                    let r = Rect::new(x as f64, y as f64, (x + rw) as f64, (y + rh) as f64);
                    if rx > 0.0 { top.fill_path(&RoundedRect::from_rect(r, rx as f64).to_path(0.1)); } else { top.fill_rect(&r); }
                    top.reset_paint_transform();
                    if layered { top.pop_layer(); }
                }
                104 => {
                    layers.push(false);
                    let [x, y, rw, rh, rx] = rd.take::<5>()?;
                    let r = Rect::new(x as f64, y as f64, (x + rw) as f64, (y + rh) as f64);
                    let p = if rx > 0.0 { RoundedRect::from_rect(r, rx as f64).to_path(0.1) } else { r.to_path(0.1) };
                    top.push_clip_layer(&p);
                }
                105 => {
                    if layers.pop() == Some(true) {
                        // close the scratch frame: render, run the effect chain, composite
                        let mut sub = subs.pop()?;
                        offs.pop();
                        let poff = offs.last().copied().unwrap_or(Affine::IDENTITY);
                        let (fx, (ox, oy, sw, sh)) = fx_params.pop()?;
                        sub.flush();
                        let mut pm = Pixmap::new(sw, sh);
                        sub.render(&mut pm, &mut st.res);
                        ellua_effects::apply(pm.data_as_u8_slice_mut(), sw as usize, sh as usize,
                            fx[0], fx[1], fx[2], fx[3], fx[4], fx[5], fx[6], fx[7], fx[8]);
                        let id = st.res.register_image(Arc::new(pm));
                        temp_images.push(id);
                        let parent: &mut RenderContext = if let Some(s) = subs.last_mut() { s } else { &mut ctx };
                        parent.set_transform(poff);
                        parent.set_paint(PBrush::Image(ImageBrush {
                            image: ImageSource::opaque_id(id),
                            sampler: ImageSampler { x_extend: Extend::Pad, y_extend: Extend::Pad, quality: ImageQuality::Low, alpha: 1.0 },
                        }));
                        parent.set_paint_transform(Affine::translate((ox as f64, oy as f64)));
                        parent.fill_rect(&Rect::new(ox as f64, oy as f64, (ox as i32 + sw as i32) as f64, (oy as i32 + sh as i32) as f64));
                        parent.reset_paint_transform();
                        parent.set_transform(poff * base);
                    } else {
                        top.pop_layer();
                    }
                }
                111 => {
                    let p = rd.take::<9>()?;
                    let [bx, by, bw, bh] = rd.take::<4>()?;
                    // frame-space bbox of the node box under the current affine
                    let corners = [
                        base * Point::new(bx as f64, by as f64),
                        base * Point::new((bx + bw) as f64, by as f64),
                        base * Point::new(bx as f64, (by + bh) as f64),
                        base * Point::new((bx + bw) as f64, (by + bh) as f64),
                    ];
                    let (mut x0, mut y0, mut x1, mut y1) = (f64::MAX, f64::MAX, f64::MIN, f64::MIN);
                    for c in corners { x0 = x0.min(c.x); y0 = y0.min(c.y); x1 = x1.max(c.x); y1 = y1.max(c.y); }
                    let ox = x0.floor() as i32;
                    let oy = y0.floor() as i32;
                    let sw = ((x1.ceil() as i32) - ox).clamp(1, 16384) as u16;
                    let sh = ((y1.ceil() as i32) - oy).clamp(1, 16384) as u16;
                    let mut sub = RenderContext::new_with(sw, sh, settings.clone());
                    let noff = Affine::translate((-ox as f64, -oy as f64));
                    sub.set_transform(noff * base);
                    subs.push(sub);
                    offs.push(noff);
                    fx_params.push((p, (ox, oy, sw, sh)));
                    layers.push(true);
                }
                106 => {
                    let [amount, seed, x, y, rw, rh] = rd.take::<6>()?;
                    let p0 = base * Point::new(x as f64, y as f64);
                    let p1 = base * Point::new((x + rw) as f64, (y + rh) as f64);
                    grains.push((amount, seed as u32, p0.x.min(p1.x).max(0.0) as usize, p0.y.min(p1.y).max(0.0) as usize,
                        (p0.x.max(p1.x).min(w as f64)) as usize, (p0.y.max(p1.y).min(h as f64)) as usize));
                }
                107 => {
                    layers.push(false);
                    let [mix, compose] = rd.take::<2>()?;
                    let mix = match mix as u32 { 1 => Mix::Multiply, 2 => Mix::Screen, 4 => Mix::Darken, 5 => Mix::Lighten, _ => Mix::Normal };
                    let compose = match compose as u32 { 1 => Compose::Plus, _ => Compose::SrcOver };
                    top.push_blend_layer(BlendMode::new(mix, compose));
                }
                108 => {
                    layers.push(false);
                    let [kind, amount] = rd.take::<2>()?;
                    let f = match kind as u32 {
                        0 => FilterFunction::Blur { radius: amount },
                        1 => FilterFunction::Brightness { amount },
                        2 => FilterFunction::Contrast { amount },
                        3 => FilterFunction::Saturate { amount },
                        4 => FilterFunction::Grayscale { amount },
                        5 => FilterFunction::Sepia { amount },
                        6 => FilterFunction::Invert { amount },
                        7 => FilterFunction::Opacity { amount },
                        _ => FilterFunction::HueRotate { angle: amount },
                    };
                    top.push_layer(None, None, None, None, Some(Filter::from_function(f)));
                }
                110 => {
                    layers.push(false);
                    let [a] = rd.take::<1>()?;
                    top.push_opacity_layer(a);
                }
                102 => {
                    let [r, g, b, a] = rd.take::<4>()?;
                    top.set_transform(off);
                    top.set_paint(color(r, g, b, a));
                    top.fill_rect(&Rect::new(0.0, 0.0, w as f64, h as f64));
                    top.set_transform(off * base);
                }
                100 => {
                    let [a, b, c, d, e, f] = rd.take::<6>()?;
                    base = Affine::new([a as f64, b as f64, c as f64, d as f64, e as f64, f as f64]);
                    top.set_transform(off * base);
                }
                101 => {
                    let [font, size, x, y, ls, wrap, leading, align, ow] = rd.take::<9>()?;
                    let [or_, og, ob, oa, emb, nruns] = rd.take::<6>()?;
                    let mut runs = Vec::with_capacity(nruns as usize);
                    for _ in 0..nruns as usize {
                        let [off, len, r, g, b, a, bold, italic] = rd.take::<8>()?;
                        runs.push(TextRun { text: sref(off, len)?, color: [r, g, b, a], bold: bold > 0.5, italic: italic > 0.5 });
                    }
                    let spec = TextSpec { font: font as usize, size, ls, wrap, leading, align: align as i32, runs };
                    build_layout(st, &spec);
                    draw_text(st, &mut ctx, base, x, y, (ow, [or_, og, ob, oa]), emb);
                }
                _ => return None,
            }
        }
        Some(())
    })();
    let result = ok.and_then(|_| {
        ctx.flush();
        let mut pm = Pixmap::new(w, h);
        ctx.render(&mut pm, &mut st.res);
        out.copy_from_slice(pm.data_as_u8_slice());
        for id in temp_images.drain(..) { st.res.destroy_image(id); }
        if let Some((amount, seed)) = grain {
            grains.push((amount, seed, 0, 0, w as usize, h as usize));
        }
        for (amount, seed, x0, y0, x1, y1) in grains {
            let amp = (amount * 255.0) as i32;
            if amp > 0 {
                for yy in y0..y1 { for xx in x0..x1 {
                    let i = yy * w as usize + xx;
                    let mut n = (i as u32).wrapping_mul(0x9E3779B9).wrapping_add(seed.wrapping_mul(0x85EBCA6B));
                    n ^= n >> 16;
                    n = n.wrapping_mul(0x7FEB352D);
                    n ^= n >> 15;
                    let d = ((n & 0xFF) as i32 - 128) * amp / 128;
                    for c in 0..3 {
                        out[i * 4 + c] = (out[i * 4 + c] as i32 + d).clamp(0, 255) as u8;
                    }
                } }
            }
        }
        Some(())
    });
    st.ctx = Some((w, h, threads, ctx));
    result
}

#[no_mangle]
pub extern "C" fn cs_font_load(path: *const c_char) -> c_int {
    let r = catch_unwind(AssertUnwindSafe(|| {
        let p = unsafe { CStr::from_ptr(path) }.to_string_lossy().into_owned();
        let mut st = state().lock().unwrap();
        if let Some(id) = st.by_path.get(&p) {
            return *id;
        }
        let Ok(bytes) = std::fs::read(&p) else { return -1 };
        let id = register(&mut st, Arc::new(bytes));
        if id >= 0 {
            st.by_path.insert(p, id);
        }
        id
    }));
    r.unwrap_or(-1)
}

#[no_mangle]
pub extern "C" fn cs_text_measure(
    font: c_int,
    size: f32,
    text: *const c_char,
    ls: f32,
    wrap: f32,
    leading: f32,
    out: *mut f32,
) -> c_int {
    let r = catch_unwind(AssertUnwindSafe(|| {
        let t = unsafe { CStr::from_ptr(text) }.to_string_lossy().into_owned();
        let mut st = state().lock().unwrap();
        let spec = TextSpec {
            font: font.max(0) as usize,
            size,
            ls,
            wrap,
            leading,
            align: 0,
            runs: vec![TextRun { text: &t, color: [1.0; 4], bold: false, italic: false }],
        };
        build_layout(&mut st, &spec);
        let (w, h) = (st.layout.full_width(), st.layout.height());
        unsafe {
            *out = w;
            *out.add(1) = h;
        }
        0
    }));
    r.unwrap_or(-1)
}

#[no_mangle]
pub extern "C" fn cs_render(
    cmds: *const f32,
    len: usize,
    strings: *const u8,
    strings_len: usize,
    w: u16,
    h: u16,
    threads: u16,
    out: *mut u8,
    out_len: usize,
) -> c_int {
    let r = catch_unwind(AssertUnwindSafe(|| {
        let cmds = unsafe { std::slice::from_raw_parts(cmds, len) };
        let strings = if strings_len == 0 { &[][..] } else { unsafe { std::slice::from_raw_parts(strings, strings_len) } };
        let out = unsafe { std::slice::from_raw_parts_mut(out, out_len) };
        let mut st = state().lock().unwrap();
        run(&mut st, cmds, strings, w, h, threads, out)
    }));
    match r {
        Ok(Some(())) => 0,
        _ => -1,
    }
}

/// Register straight-alpha RGBA8 pixels (love ImageData layout) as a scene image.
fn to_premul(src: &[u8], n: usize, premul: bool) -> Vec<PremulRgba8> {
    let mut px = Vec::with_capacity(n);
    for i in 0..n {
        let a = src[i * 4 + 3];
        if premul || a == 255 {
            px.push(PremulRgba8 { r: src[i * 4], g: src[i * 4 + 1], b: src[i * 4 + 2], a });
        } else {
            let a32 = a as u32;
            let pm = |c: u8| ((c as u32 * a32 + 127) / 255) as u8;
            px.push(PremulRgba8 { r: pm(src[i * 4]), g: pm(src[i * 4 + 1]), b: pm(src[i * 4 + 2]), a });
        }
    }
    px
}

/// premul != 0: pixels are already premultiplied (blitz, vello, love canvases).
#[no_mangle]
pub extern "C" fn cs_image_register(rgba: *const u8, w: u16, h: u16, premul: c_int) -> c_int {
    let r = catch_unwind(AssertUnwindSafe(|| {
        let n = w as usize * h as usize;
        let src = unsafe { std::slice::from_raw_parts(rgba, n * 4) };
        let px = to_premul(src, n, premul != 0);
        let mut st = state().lock().unwrap();
        let id = st.res.register_image(Arc::new(Pixmap::from_parts(px, w, h)));
        st.images.push((id, w, h));
        (st.images.len() - 1) as c_int
    }));
    r.unwrap_or(-1)
}

/// Replace the pixels of a registered image slot (html textures, video frames).
#[no_mangle]
pub extern "C" fn cs_image_update(slot: c_int, rgba: *const u8, w: u16, h: u16, premul: c_int) -> c_int {
    let r = catch_unwind(AssertUnwindSafe(|| {
        let n = w as usize * h as usize;
        let src = unsafe { std::slice::from_raw_parts(rgba, n * 4) };
        let px = to_premul(src, n, premul != 0);
        let mut st = state().lock().unwrap();
        let Some(&(old, _, _)) = st.images.get(slot as usize) else { return -1 };
        st.res.destroy_image(old);
        let id = st.res.register_image(Arc::new(Pixmap::from_parts(px, w, h)));
        st.images[slot as usize] = (id, w, h);
        0
    }));
    r.unwrap_or(-1)
}
