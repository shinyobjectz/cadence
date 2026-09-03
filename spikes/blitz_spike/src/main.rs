// Blitz spike: CSS-laid-out HTML fragment → RGBA buffer, no Chrome.
// Validates (1) headless render works, (2) determinism, (3) per-frame rebuild cost
// (worst-case animation strategy: re-parse + re-layout + repaint every frame).

use anyrender::render_to_buffer;
use anyrender_vello_cpu::VelloCpuImageRenderer;
use blitz_dom::DocumentConfig;
use blitz_html::HtmlDocument;
use blitz_paint::paint_scene;
use blitz_traits::shell::{ColorScheme, Viewport};
use std::time::Instant;

const W: u32 = 1080;
const H: u32 = 300;

fn lower_third_html(x_off: f64, opacity: f64) -> String {
    format!(
        r#"<!DOCTYPE html><html><head><style>
        body {{ margin: 0; width: {W}px; height: {H}px; background: transparent;
               font-family: Helvetica, Arial, sans-serif; }}
        .wrap {{ display: flex; align-items: center; height: 100%;
                transform: translateX({x_off}px); opacity: {opacity}; }}
        .bar {{ width: 10px; height: 160px; background: #ff8a3d; border-radius: 5px;
               margin: 0 24px 0 48px; }}
        .card {{ background: linear-gradient(135deg, rgba(20,22,31,0.92), rgba(40,44,62,0.92));
                border: 1px solid rgba(255,255,255,0.15); border-radius: 18px;
                padding: 24px 40px; box-shadow: 0 12px 40px rgba(0,0,0,0.4); }}
        .name {{ color: #ffffff; font-size: 44px; font-weight: 700; }}
        .role {{ color: #9fd8ff; font-size: 26px; margin-top: 6px;
                display: flex; gap: 12px; align-items: center; }}
        .dot {{ width: 10px; height: 10px; border-radius: 5px; background: #4fc978; }}
        </style></head><body>
        <div class="wrap"><div class="bar"></div>
          <div class="card"><div class="name">Shane Objectz</div>
            <div class="role"><div class="dot"></div>Founder, ellua — CSS without Chrome</div>
          </div></div></body></html>"#
    )
}

fn render(html: &str) -> Vec<u8> {
    let mut document = HtmlDocument::from_html(
        html,
        DocumentConfig {
            viewport: Some(Viewport::new(W, H, 1.0, ColorScheme::Dark)),
            ..Default::default()
        },
    );
    document.as_mut().resolve(0.0);
    render_to_buffer::<VelloCpuImageRenderer, _>(
        |scene| paint_scene(scene, document.as_mut(), 1.0, W, H, 0, 0),
        W,
        H,
    )
}

fn main() {
    let a = render(&lower_third_html(0.0, 1.0));
    let b = render(&lower_third_html(0.0, 1.0));
    println!(
        "DETERMINISM {} ({} bytes)",
        if a == b { "PASS" } else { "FAIL" },
        a.len()
    );
    std::fs::write("/tmp/blitz_frame.raw", &a).unwrap();

    // worst-case animation: full re-parse + layout + paint per frame
    let n = 60;
    let start = Instant::now();
    let mut acc = 0u64;
    for i in 0..n {
        let t = i as f64 / 30.0;
        let buf = render(&lower_third_html(-40.0 + t * 40.0, (t * 2.0).min(1.0)));
        acc ^= buf[0] as u64;
    }
    let dt = start.elapsed().as_secs_f64();
    println!(
        "REBUILD-PER-FRAME {n} frames in {dt:.2}s = {:.1} fps at {W}x{H} (acc={acc})",
        n as f64 / dt
    );
}
