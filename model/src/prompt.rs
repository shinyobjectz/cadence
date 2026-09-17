//! Prompt assembly. The model sees: the Cadence rules it must respect, the
//! output contract (SEARCH/REPLACE blocks), timeline facts the toolchain
//! already knows (node ids, kinds, lint findings) and the comp source.

use crate::validate::Facts;

pub const SYSTEM: &str = r##"You are the Cadence editor model. You edit Lua compositions for the Cadence renderer.

Cadence rules you must keep:
- A comp is a pure function of time: no clocks, no I/O, no randomness outside seeded helpers.
- Times are seconds. `duration` in the header is static; scripts must not run past it.
- Nodes are declared in `scene = function(s) ... end`; z-order is declaration order.
- Motion is recorded once in `s:script(function(t) ... end)` with t:tween(node, seconds, {props}, "ease"), t:wait(seconds), t:parallel(fn, fn, ...), t:set(node, {props}).
- Eases: linear quadOut cubicInOut sineInOut sineOut expoOut backOut elastic spring and friends. Never use overshoot eases (back/elastic/spring) on `opacity`.
- Colors are "#rrggbb" strings. Positions are pixels in the comp's width x height.

Output contract — reply ONLY with edit blocks, nothing else:
<<<<<<< SEARCH
exact existing lines (copy them verbatim, including indentation)
=======
replacement lines
>>>>>>> REPLACE

Use several blocks for several places. Keep edits minimal and local: change only what the instruction asks. If no change is needed reply with the single word NO_CHANGE."##;

/// The user turn: instruction + facts + source.
pub fn user_turn(instruction: &str, facts: Option<&Facts>, source: &str, feedback: Option<&str>) -> String {
    let mut s = String::new();
    s.push_str("Instruction: ");
    s.push_str(instruction.trim());
    s.push_str("\n\n");
    if let Some(f) = facts {
        s.push_str("Timeline facts (from the toolchain):\n");
        s.push_str(&f.summary());
        s.push('\n');
    }
    if let Some(fb) = feedback {
        s.push_str("Your previous edit failed validation:\n");
        s.push_str(fb.trim());
        s.push_str("\nFix it. Reply with edit blocks against the ORIGINAL file below.\n\n");
    }
    s.push_str("File (comp.lua):\n```lua\n");
    s.push_str(source);
    if !source.ends_with('\n') {
        s.push('\n');
    }
    s.push_str("```\n");
    s
}

/// Qwen2.5 / ChatML formatting. Other chat templates can be added here when a
/// different base model is used; the contract stays the same.
pub fn chat_format(system: &str, turns: &[(String, String)]) -> String {
    let mut p = String::new();
    p.push_str("<|im_start|>system\n");
    p.push_str(system);
    p.push_str("<|im_end|>\n");
    for (user, assistant) in turns {
        p.push_str("<|im_start|>user\n");
        p.push_str(user);
        p.push_str("<|im_end|>\n<|im_start|>assistant\n");
        if !assistant.is_empty() {
            p.push_str(assistant);
            p.push_str("<|im_end|>\n");
        }
    }
    p
}
