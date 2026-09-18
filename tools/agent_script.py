# Video 2: how facts land on real pixels, and how an edit comes back out.
CHAPTERS = [
 dict(id="open", n="", title="Facts on real footage",
   say="This is a real clip. Fourteen seconds, nothing added. Everything you are about to "
       "see drawn on top of it was measured from these pixels and written down as facts."),
 dict(id="track", n="01", title="The tracker",
   say="A detector proposes a box, and SAM two propagates the mask forwards and backwards "
       "from that seed. Two entities survive here: a bottle, and a gloved hand. These boxes "
       "are the tracker's own output. The loose one is exactly why every fact carries a "
       "confidence instead of pretending to be exact."),
 dict(id="thirds", n="02", title="A position becomes a word",
   say="Here is the translation. A box is a rectangle of numbers, and the log refuses to "
       "store numbers. It stores the word an editor would say out loud. While the bottle "
       "sits in the middle third, the log says so, and records the interval it held for."),
 dict(id="motion", n="03", title="So does a velocity",
   say="Movement gets the same treatment. Not pixels per second, but a direction and an "
       "adverb. Moving left, fast, from seven point nine seconds to ten point four."),
 dict(id="event", n="04", title="And the moment itself",
   say="Then the moment. The gap between the two masks opens at eight and a half seconds, "
       "and that becomes an event, carrying the producer that measured it and how sure it "
       "was. Nothing in this log claims to be exact, because nothing in it is."),
 dict(id="edit", n="05", title="The agent edits",
   say="Now the agent works. It never looks at the picture. It asks the log when the bottle "
       "changed hands, gets eight point five, and asserts that the caption belongs there."),
 dict(id="proof", n="06", title="And checks its own work",
   say="Then it proves what it did. Render both versions and hash every frame. One hundred "
       "of three hundred and fifty eight differ, in exactly two windows: where the caption "
       "used to be, and where it is now. Nothing else moved."),
 dict(id="close", n="", title="",
   say="Real pixels, measured into words, edited by assertion."),
]
