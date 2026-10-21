# The one face: a black glitter full-face motorcycle helmet, seen three-quarters on and facing right,
# whose dark visor lights up with two cute glowing eyes. The same helmet is the phone's big face, the
# rider's head on the PC and the app's icons. It is drawn as SVG, so it stays sharp at any size. Its
# eyes, brows and the colour shimmering across its visor follow what it is doing: listening,
# thinking, typing, clicking, scrolling, opening something, researching, waiting for a yes, happy,
# laughing, worried, or asleep.
import json
import math
import random

import paint_jobs

MOODS = ("idle", "listening", "thinking", "typing", "clicking", "scrolling", "opening", "researching",
         "waiting", "happy", "laughing", "oops", "sleeping", "cool")

# The eyes, brows and extras of each mood.
MOOD_PARTS = {
    "idle": ("eyes-open", None, ()),
    "listening": ("eyes-big", "brows-raised", ("waves",)),
    "thinking": ("eyes-open", "brows-quizzical", ("dots",)),
    "typing": ("eyes-open", "brows-determined", ()),
    "clicking": ("eyes-focus", "brows-determined", ()),
    "scrolling": ("eyes-open", "brows-raised", ()),
    "opening": ("eyes-star", "brows-raised", ("sparkles",)),
    "researching": ("eyes-open", "brows-quizzical", ("monocle",)),
    "waiting": ("eyes-big", "brows-worried", ()),
    "happy": ("eyes-happy", None, ("sparkles",)),
    "laughing": ("eyes-laugh", None, ("tears",)),
    "oops": ("eyes-shock", "brows-worried", ("sweat",)),
    "sleeping": ("eyes-sleep", None, ("zzz",)),
    "cool": ("eyes-shades", None, ()),  # sunglasses, around noon
}

# The visor's colours, like the iridium visors of real helmets: its sheen, the sheen's edge, and the
# glow around the eyes. Each mood shimmers in its own; asleep, the visor is plain dark smoke.
VISORS = {
    "blue": ("#38BDF8", "#6366F1", "#7DD3FC"),
    "ice": ("#22D3EE", "#3B82F6", "#67E8F9"),
    "violet": ("#A78BFA", "#6366F1", "#C4B5FD"),
    "green": ("#34D399", "#3B82F6", "#6EE7B7"),
    "fire": ("#FB923C", "#EF4444", "#FDBA74"),
    "gold": ("#FDE047", "#F59E0B", "#FDE68A"),
    "emerald": ("#4ADE80", "#2563EB", "#86EFAC"),
    "silver": ("#E2E8F0", "#94A3B8", "#E2E8F0"),
    "sunset": ("#FACC15", "#F97316", "#FDE68A"),
    "red": ("#FB7185", "#E11D48", "#FDA4AF"),
    "smoke": ("#334155", "#1E293B", "#A5B4FC"),
}
MOOD_VISOR = {"idle": "blue", "listening": "ice", "thinking": "violet", "typing": "green", "clicking": "fire",
              "scrolling": "blue", "opening": "gold", "researching": "emerald", "waiting": "silver",
              "happy": "gold", "laughing": "sunset", "oops": "red", "sleeping": "smoke", "cool": "sunset"}

# Moods whose eyes look their own way (-1..1 across, -1..1 down); in the others the eyes follow the
# PC's mouse pointer.
MOOD_LOOK = {"thinking": (0.7, -0.8), "researching": (0.5, 0.1), "typing": (0.0, 0.9), "scrolling": (0.0, 1.0)}
LOOKING_EYES = ("eyes-open", "eyes-big", "eyes-focus", "eyes-shock")
LOOK_REACH = (7, 5)  # how far the eyes move inside the visor at look 1, across and down

EYE = "#EAF8FF"  # the eyes' own light, the same in every mood
SVG_OPEN = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">'

# The helmet three-quarters on, facing right: the shell, its visor, and the spoiler at the back.
SHELL = ("M100 16 C134 14 162 27 176 48 C184 61 188 74 188 86 L189 112 C189 122 186 130 183 136 "
         "C190 147 193 159 188 170 L184 177 C166 181 146 181 128 177 C108 173 88 171 70 170 "
         "C54 169 40 161 32 149 C22 135 18 113 20 93 C22 71 30 49 46 35 C62 23 80 16 100 16 Z")
SPOILER = "M70 24 C54 20 32 22 10 31 C11 38 16 43 24 46 C33 50 41 55 47 63 C52 48 60 35 70 24 Z"
VISOR = ("M95 66 C121 60 151 58 176 62 C182 64 186 70 187 78 L189 110 C189 120 184 130 176 136 "
         "C160 136 140 132 124 128 C112 125 102 120 97 112 C93 98 92 80 95 66 Z")
OUTLINE = SHELL  # what the light edge follows (with the spoiler)

NEAR_EYE, FAR_EYE = (129, 98, 1.0), (166, 98, 0.8)  # each eye's middle, and how wide it looks from this side


def _glitter():
    # The paint's metal flakes: many around the shine on top, a few everywhere else, never on the visor.
    rnd = random.Random(2026)
    dots = []
    while len(dots) < 120:
        if len(dots) < 80:
            x, y = rnd.gauss(122, 26), rnd.gauss(36, 16)
        else:
            x, y = rnd.uniform(24, 184), rnd.uniform(18, 178)
        inside = ((x - 102) / 82) ** 2 + ((y - 98) / 82) ** 2 < 1 and y > 18
        on_visor = 92 < x < 190 and 56 < y < 138 and (y - 60) > (95 - x) * 2.2
        on_vent = 112 < x < 178 and 32 < y < 50
        if inside and not on_visor and not on_vent:
            near = math.exp(-math.hypot(x - 126, y - 32) / 30)
            dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{rnd.uniform(0.45, 1.15) * (0.7 + 0.6 * near):.2f}" '
                        f'fill="{rnd.choice(("#FFFFFF", "#FFFFFF", "#C7D2FE", "#A5B4FC"))}" '
                        f'opacity="{min(1.0, rnd.uniform(0.12, 0.42) + 0.6 * near):.2f}"/>')
    return "".join(dots)


def _twinkle(x, y, size, colour="#FFFFFF"):
    s = size
    return (f'<path d="M{x} {y - s} Q{x + s * 0.18:.2f} {y - s * 0.18:.2f} {x + s} {y} Q{x + s * 0.18:.2f} {y + s * 0.18:.2f} '
            f'{x} {y + s} Q{x - s * 0.18:.2f} {y + s * 0.18:.2f} {x - s} {y} Q{x - s * 0.18:.2f} {y - s * 0.18:.2f} {x} {y - s} Z" '
            f'fill="{colour}"/>')


def _star(x, y, scale):
    return (f'<path transform="translate({x} {y}) scale({scale})" d="M0 -11 Q2.2 -2.2 11 0 Q2.2 2.2 0 11 '
            'Q-2.2 2.2 -11 0 Q-2.2 -2.2 0 -11 Z" fill="#FFF6C2" stroke="#F59E0B" stroke-width="1.5"/>')


def _shell_defs(job: dict) -> str:
    # The shell in a paint job's colours (see paint_jobs.py; the first is deep black), lit from above and
    # in front, its far side in shadow.
    stops = paint_jobs.stops
    return (f'<radialGradient id="hm-shell" cx="0.64" cy="0.2" r="0.9">{stops(job["shell"], (0, 0.28, 0.65, 1))}</radialGradient>'
            '<linearGradient id="hm-side" x1="0" y1="0" x2="1" y2="0">'
            '<stop offset="0" stop-color="#000000" stop-opacity="0.55"/><stop offset="0.45" stop-color="#000000" stop-opacity="0"/>'
            '</linearGradient>'
            f'<linearGradient id="hm-panel" x1="0" y1="0" x2="1" y2="1">{stops(job["panel"], (0, 0.5, 1))}</linearGradient>'
            f'<linearGradient id="hm-jaw" x1="0" y1="0" x2="0" y2="1">{stops(job["jaw"], (0, 1))}</linearGradient>'
            f'<linearGradient id="hm-chin" x1="0" y1="0" x2="0" y2="1">{stops(job["chin"], (0, 0.35, 1))}</linearGradient>')


def base_defs(paint: str = None) -> str:
    return _shell_defs(paint_jobs.paint(paint)) + OTHER_DEFS


OTHER_DEFS = (
    '<radialGradient id="hm-shine" cx="0.5" cy="0.5" r="0.5">'
    '<stop offset="0" stop-color="#FFFFFF" stop-opacity="0.6"/><stop offset="0.35" stop-color="#C7D2FE" stop-opacity="0.22"/>'
    '<stop offset="1" stop-color="#C7D2FE" stop-opacity="0"/></radialGradient>'
    '<linearGradient id="hm-metal" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#9AA2B2"/><stop offset="1" stop-color="#3B4150"/></linearGradient>'
    '<linearGradient id="hm-plate" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#7C8496" stop-opacity="0.9"/><stop offset="1" stop-color="#2A2F3A" stop-opacity="0.9"/>'
    '</linearGradient>'
    # the visor: dark smoke, a pale sky reflected across its top
    '<linearGradient id="hm-visor" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#1B2338"/><stop offset="0.5" stop-color="#0A0E19"/><stop offset="1" stop-color="#05070C"/>'
    '</linearGradient>'
    '<linearGradient id="hm-sky" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#FFFFFF" stop-opacity="0.34"/><stop offset="1" stop-color="#FFFFFF" stop-opacity="0.04"/>'
    '</linearGradient>'
    '<radialGradient id="hm-blush" cx="0.5" cy="0.5" r="0.5">'
    '<stop offset="0" stop-color="#FF9BD0" stop-opacity="0.75"/><stop offset="1" stop-color="#FF9BD0" stop-opacity="0"/>'
    '</radialGradient>'
    '<linearGradient id="hm-drop" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#D6F3FF"/><stop offset="1" stop-color="#3BA4F5"/></linearGradient>'
)


def _paints(visor, tag=""):
    # A visor's colours as gradients: its sheen from the front, the sheen along its bottom, the glow
    # around the eyes, and the glow along lit lines. The phone page has every visor's, named by tag.
    sheen, edge, glow = VISORS[visor]
    stop = '<stop offset="{o}" stop-color="{col}" stop-opacity="{a}"/>'
    return (f'<linearGradient id="hm-sheen{tag}" gradientUnits="userSpaceOnUse" x1="100" y1="74" x2="189" y2="122">'
            + stop.format(o=0, col=sheen, a=0) + stop.format(o=0.42, col=sheen, a=0)
            + stop.format(o=0.76, col=sheen, a=0.55) + stop.format(o=1, col=edge, a=0.95) + '</linearGradient>'
            f'<linearGradient id="hm-low{tag}" gradientUnits="userSpaceOnUse" x1="0" y1="100" x2="0" y2="136">'
            + stop.format(o=0, col=edge, a=0) + stop.format(o=0.5, col=edge, a=0)
            + stop.format(o=1, col=sheen, a=0.6) + '</linearGradient>'
            f'<radialGradient id="hm-glow{tag}" cx="0.5" cy="0.5" r="0.5">'
            + stop.format(o=0, col=glow, a=0.85) + stop.format(o=0.5, col=glow, a=0.3)
            + stop.format(o=1, col=glow, a=0) + '</radialGradient>'
            f'<linearGradient id="hm-line{tag}" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="200" y2="0">'
            + stop.format(o=0, col=glow, a=0.32) + stop.format(o=1, col=glow, a=0.32) + '</linearGradient>')


def _defs(visor):
    return "<defs>" + base_defs() + _paints(visor) + "</defs>"


SHELL_ART = (
    # the spoiler at the back, the shell and its darker far side
    f'<path d="{SPOILER}" fill="url(#hm-chin)" stroke="#4A5160" stroke-width="1.1" stroke-linejoin="round"/>'
    '<path d="M12 31 C30 25 50 22 68 25" fill="none" stroke="#C7D2FE" stroke-opacity="0.45" stroke-width="1.2" stroke-linecap="round"/>'
    f'<path d="{SHELL}" fill="url(#hm-shell)"/>'
    f'<path d="{SHELL}" fill="url(#hm-side)"/>'
    # the back panel running down from the spoiler, with its vents
    '<path d="M47 35 C40 60 35 100 39 128 C43 150 54 164 70 170 C65 140 63 100 67 72 C69 56 64 44 56 33 Z" fill="url(#hm-panel)" '
    'stroke="#3B4150" stroke-width="0.9"/>'
    '<path d="M46 82 L63 72 M45 96 L62 86 M45 110 L62 100 M46 124 L62 114" stroke="#050608" stroke-width="2.6" stroke-linecap="round"/>'
    '<path d="M46 84.5 L63 74.5 M45 98.5 L62 88.5 M45 112.5 L62 102.5 M46 126.5 L62 116.5" stroke="#7C8496" stroke-opacity="0.55" '
    'stroke-width="0.9" stroke-linecap="round"/>'
    # the chin bar: its upper face lit, a sharp crease, and its underside in shadow
    '<path d="M97 114 C104 124 116 129 130 132 C150 136 170 139 183 136 C187 142 189 147 189 152 C160 149 128 140 97 114 Z" '
    'fill="url(#hm-jaw)"/>'
    '<path d="M98 116 C122 136 154 148 189 153" fill="none" stroke="#8A92A3" stroke-opacity="0.55" stroke-width="1.2" stroke-linecap="round"/>'
    # broad soft reflections over the dome and along the chin, the lamp's shine with its sparkle
    '<path d="M70 36 C88 24 114 19 140 24 C118 26 96 33 82 44 C74 51 68 58 64 66 C60 56 62 45 70 36 Z" fill="#FFFFFF" opacity="0.1"/>'
    '<path d="M112 158 C132 165 156 168 182 166" fill="none" stroke="#FFFFFF" stroke-opacity="0.1" stroke-width="5" stroke-linecap="round"/>'
    + _glitter()
    + '<circle cx="126" cy="31" r="24" fill="url(#hm-shine)"/>'
    '<ellipse cx="126" cy="31" rx="7.5" ry="4.2" fill="#FFFFFF" transform="rotate(-12 126 31)"/>'
    + _twinkle(108, 26, 4.6) + _twinkle(144, 23, 3.4) + _twinkle(98, 40, 2.6, "#C7D2FE")
    # a sharp crease from the visor back to the spoiler, and one down the jaw
    + '<path d="M95 66 C80 58 68 46 58 31" fill="none" stroke="#6B7385" stroke-opacity="0.7" stroke-width="1.3" stroke-linecap="round"/>'
    '<path d="M97 114 C88 132 80 152 74 169" fill="none" stroke="#4A5160" stroke-opacity="0.65" stroke-width="1.2" stroke-linecap="round"/>'
    # the vents: winged on the brow, slots on the chin's point
    '<path d="M116 46 C126 39 138 36 150 36 C160 36 168 38 175 42 L171 47 C163 44 157 43 150 43 C141 43 131 45 123 50 Z" '
    'fill="url(#hm-metal)"/>'
    '<rect x="145" y="36.5" width="11" height="5" rx="1.6" fill="#0A0B0F"/>'
    '<path d="M174 154 L187 155 L186 170 L176 172 Z" fill="#050608" stroke="#3B4150" stroke-width="1.1"/>'
    '<path d="M176 159 L186.5 159.5 M176.5 164 L186.3 164.5" stroke="#2E3340" stroke-width="1.5"/>'
    # the rubber trim along the bottom, and a cool rim of light round the back and the dome
    '<path d="M184 177 C166 181 146 181 128 177 C108 173 88 171 70 170 C54 169 40 161 32 149" fill="none" stroke="#2E3340" '
    'stroke-width="2.6" stroke-linecap="round"/>'
    '<path d="M23 112 C22 84 30 56 48 38 C64 26 82 19 100 19" fill="none" stroke="#A5B4FC" stroke-opacity="0.35" '
    'stroke-width="2.2" stroke-linecap="round"/>'
    '<path d="M150 22 C164 30 176 44 183 62" fill="none" stroke="#FFFFFF" stroke-opacity="0.32" stroke-width="1.6" stroke-linecap="round"/>'
)

FRONT_ART = (
    # the visor in its dark rim, its pivot plate and screw, its colour shimmering from the front, and
    # the sky and a window reflected on it
    f'<path d="{VISOR}" fill="#040506" transform="translate(142 98) scale(1.045 1.08) translate(-142 -98)"/>'
    f'<path d="{VISOR}" fill="url(#hm-visor)"/>'
    f'<path class="sheen sheen-a" d="{VISOR}" fill="url(#hm-sheen)"/>'
    f'<path class="sheen sheen-b" d="{VISOR}" fill="url(#hm-low)"/>'
    '<path d="M97 70 C122 63 152 61 176 65 C181 67 184 71 185 76 C160 72 128 73 98 82 Z" fill="url(#hm-sky)"/>'
    '<path d="M100 67 C124 61 152 60 176 63" fill="none" stroke="#FFFFFF" stroke-opacity="0.45" stroke-width="1.2" stroke-linecap="round"/>'
    '<path d="M180 86 L184 86 L185.5 106 L181.5 108 Z" fill="#FFFFFF" opacity="0.18"/>'
    '<path d="M91 63 L106 61 L104 88 L93 95 Z" fill="url(#hm-plate)" stroke="#1B1E25" stroke-width="0.8"/>'
    '<circle cx="98.5" cy="76" r="2.3" fill="#C9D1DE"/><circle cx="98.5" cy="76" r="1" fill="#3B4150"/>'
    '<ellipse cx="121" cy="118" rx="10" ry="5" fill="url(#hm-blush)"/>'
    '<ellipse cx="170" cy="120" rx="8" ry="4.5" fill="url(#hm-blush)"/>'
)


def _eye_pair(draw):
    # Both eyes, the far one narrower, as seen from this side.
    return "".join(draw(cx, cy, k) for cx, cy, k in (NEAR_EYE, FAR_EYE))


def _ovals(rx, ry, dy=0.0, shine=False, pupil=False):
    def eye(cx, cy, k):
        cy += dy
        out = (f'<g class="eye"><ellipse class="glow" cx="{cx}" cy="{cy}" rx="{rx * 2 * k:.1f}" ry="{ry * 1.65:.1f}" '
               f'fill="url(#hm-glow)"/><ellipse cx="{cx}" cy="{cy}" rx="{rx * k:.1f}" ry="{ry}" fill="{EYE}"/>')
        if shine:
            out += (f'<circle cx="{cx + rx * 0.34 * k:.1f}" cy="{cy - ry * 0.42:.1f}" r="{rx * 0.28 * k:.1f}" fill="#FFFFFF"/>'
                    f'<circle cx="{cx - rx * 0.3 * k:.1f}" cy="{cy + ry * 0.4:.1f}" r="{rx * 0.13 * k:.1f}" fill="#FFFFFF"/>')
        if pupil:
            out += f'<ellipse cx="{cx}" cy="{cy}" rx="{rx * 0.32 * k:.1f}" ry="{ry * 0.32:.1f}" fill="#0B1020"/>'
        return out + '</g>'
    return _eye_pair(eye)


def _lines(make, width):
    # Lit strokes for each eye: a soft glow of the mood's colour under the eye's own light.
    d = " ".join(make(cx, cy, k) for cx, cy, k in (NEAR_EYE, FAR_EYE))
    return (f'<path class="glow-line" d="{d}" fill="none" stroke="url(#hm-line)" stroke-width="{width * 2.2:.1f}" '
            f'stroke-linecap="round" stroke-linejoin="round"/><path d="{d}" fill="none" stroke="{EYE}" '
            f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"/>')


def _shades():
    # Sunglasses over both eyes: dark lenses with a shine, and a bridge between them.
    lenses = []
    for cx, cy, k in (NEAR_EYE, FAR_EYE):
        w = 18 * k
        lenses.append(f'<path d="M{cx - w:.1f} {cy - 10} Q{cx:.1f} {cy - 14} {cx + w:.1f} {cy - 10} Q{cx + w + 1:.1f} {cy + 11} {cx:.1f} {cy + 13} '
                      f'Q{cx - w - 1:.1f} {cy + 11} {cx - w:.1f} {cy - 10} Z" fill="#0B1026" stroke="#1E293B" stroke-width="2"/>'
                      f'<path d="M{cx - w * 0.55:.1f} {cy - 5} L{cx - w * 0.1:.1f} {cy - 6}" stroke="#FFFFFF" stroke-opacity="0.75" '
                      f'stroke-width="3" stroke-linecap="round"/>')
    (nx, ny, nk), (fx, fy, fk) = NEAR_EYE, FAR_EYE
    bridge = (f'<path d="M{nx + 18 * nk:.1f} {ny - 8} Q{(nx + fx) / 2:.1f} {ny - 12} {fx - 18 * fk:.1f} {fy - 8}" fill="none" '
              f'stroke="#0B1026" stroke-width="3.5" stroke-linecap="round"/>')
    return '<g class="eye shades">' + "".join(lenses) + bridge + '</g>'


def _star_eyes():
    def eye(cx, cy, k):
        s = 12
        return (f'<g class="eye"><ellipse class="glow" cx="{cx}" cy="{cy}" rx="{22 * k:.1f}" ry="22" fill="url(#hm-glow)"/>'
                f'<path transform="translate({cx} {cy}) scale({k:.2f} 1)" d="M0 {-s} Q{s * 0.22:.1f} {-s * 0.22:.1f} {s} 0 '
                f'Q{s * 0.22:.1f} {s * 0.22:.1f} 0 {s} Q{-s * 0.22:.1f} {s * 0.22:.1f} {-s} 0 Q{-s * 0.22:.1f} {-s * 0.22:.1f} 0 {-s} Z" '
                f'fill="{EYE}"/></g>')
    return _eye_pair(eye)


PARTS = {
    "eyes-open": _ovals(10.5, 14),
    "eyes-big": _ovals(12.5, 17, shine=True),
    "eyes-focus": _ovals(11.5, 8, dy=2),
    "eyes-shock": _ovals(11, 13, pupil=True),
    "eyes-star": _star_eyes(),
    "eyes-shades": _shades(),
    "eyes-happy": _lines(lambda x, y, k: f"M{x - 12 * k:.1f} {y + 6} Q{x} {y - 13} {x + 12 * k:.1f} {y + 6}", 6.5),
    "eyes-laugh": _lines(lambda x, y, k: f"M{x - 10 * k:.1f} {y - 9} L{x + 8 * k:.1f} {y} L{x - 10 * k:.1f} {y + 9}"
                         if k == 1 else f"M{x + 10 * k:.1f} {y - 9} L{x - 8 * k:.1f} {y} L{x + 10 * k:.1f} {y + 9}", 6),
    "eyes-sleep": _lines(lambda x, y, k: f"M{x - 11 * k:.1f} {y - 1} Q{x} {y + 9} {x + 11 * k:.1f} {y - 1}", 5),
    "brows-raised": _lines(lambda x, y, k: f"M{x - 11 * k:.1f} {y - 22} Q{x} {y - 30} {x + 11 * k:.1f} {y - 24}", 4.2),
    "brows-determined": _lines(lambda x, y, k: f"M{x - 11 * k:.1f} {y - 27} Q{x} {y - 25} {x + 11 * k:.1f} {y - 20}"
                               if k == 1 else f"M{x + 11 * k:.1f} {y - 27} Q{x} {y - 25} {x - 11 * k:.1f} {y - 20}", 4.2),
    "brows-worried": _lines(lambda x, y, k: f"M{x - 11 * k:.1f} {y - 20} Q{x} {y - 25} {x + 11 * k:.1f} {y - 29}"
                            if k == 1 else f"M{x + 11 * k:.1f} {y - 20} Q{x} {y - 25} {x - 11 * k:.1f} {y - 29}", 4.2),
    "brows-quizzical": _lines(lambda x, y, k: f"M{x - 11 * k:.1f} {y - 22} Q{x} {y - 24} {x + 11 * k:.1f} {y - 22}"
                              if k == 1 else f"M{x - 11 * k:.1f} {y - 26} Q{x} {y - 35} {x + 11 * k:.1f} {y - 29}", 4.2),
}
EXTRAS = {
    "dots": ('<g class="fx-dots" fill="#FFFFFF" stroke="#94A3B8" stroke-width="1.5">'
             '<circle cx="170" cy="26" r="4.2"/><circle cx="181" cy="15" r="5.4"/><circle cx="192" cy="6" r="5.8"/></g>'),
    "monocle": ('<g class="fx-monocle"><circle cx="129" cy="98" r="19" fill="#FFFFFF" fill-opacity="0.12" '
                'stroke="#E8B04B" stroke-width="4"/><path d="M146 108 Q156 136 146 176" fill="none" '
                'stroke="#E8B04B" stroke-width="2.3" stroke-linecap="round"/></g>'),
    "sparkles": f'<g class="fx-sparkles">{_star(16, 70, 0.9)}{_star(190, 30, 0.8)}{_star(28, 178, 0.65)}</g>',
    "tears": ('<g class="fx-tears"><path d="M86 104 Q78 120 86 127 Q95 131 97 119 Q97 110 86 104 Z" fill="url(#hm-drop)"/>'
              '<path d="M194 104 Q201 118 194 125 Q186 129 185 117 Q185 109 194 104 Z" fill="url(#hm-drop)"/></g>'),
    "sweat": '<path class="fx-sweat" d="M160 12 Q152 27 157 34 Q165 39 169 31 Q171 23 160 12 Z" fill="url(#hm-drop)"/>',
    "zzz": ('<g class="fx-zzz" font-family="Segoe UI, Arial, sans-serif" font-weight="700" fill="#C7D2FE">'
            '<text x="160" y="40" font-size="22">z</text><text x="174" y="24" font-size="17">z</text>'
            '<text x="186" y="11" font-size="13">z</text></g>'),
    "waves": ('<g class="fx-waves" fill="none" stroke="#93C5FD" stroke-width="4" stroke-linecap="round">'
              '<path d="M8 84 Q0 102 8 120"/><path d="M195 84 Q201 102 195 120"/></g>'),
}
PARTS.update(EXTRAS)
BLINK = "".join(f'<rect x="{cx - 12 * k:.1f}" y="{cy - 2}" width="{24 * k:.1f}" height="4" rx="2" fill="{EYE}"/>'
                for cx, cy, k in (NEAR_EYE, FAR_EYE))


def face_svg(mood: str = "idle", look=(0.0, 0.0), blink: bool = False, extras: bool = True) -> str:
    # One expression as a standalone SVG, for the PC rider, the PC window and the icons. Without
    # extras, the dots, sparkles and drops around the head are left out (the rider draws them apart).
    eyes, brows, mood_extras = MOOD_PARTS.get(mood, MOOD_PARTS["idle"])
    lx, ly = MOOD_LOOK.get(mood, look)
    front = FRONT_ART if mood != "sleeping" else FRONT_ART.replace('class="sheen', 'opacity="0" class="sheen')
    parts = [_defs(MOOD_VISOR.get(mood, "blue")), SHELL_ART, front]
    if eyes in LOOKING_EYES:
        parts.append(f'<g transform="translate({lx * LOOK_REACH[0]:.1f} {ly * LOOK_REACH[1]:.1f})">'
                     f'{BLINK if blink else PARTS[eyes]}</g>')
    else:
        parts.append(PARTS[eyes])
    if brows:
        parts.append(PARTS[brows])
    if extras:
        parts += [PARTS[extra] for extra in mood_extras]
    return SVG_OPEN + "".join(parts) + "</svg>"


def extras_svg(mood: str) -> str:
    # Only the mood's extras around the head (with what they are painted with), or "" when it has none.
    extras = MOOD_PARTS.get(mood, MOOD_PARTS["idle"])[2]
    if not extras:
        return ""
    return SVG_OPEN + "<defs>" + base_defs() + "</defs>" + "".join(PARTS[extra] for extra in extras) + "</svg>"


def outline_svg(width: float = 6.0) -> str:
    # The helmet's silhouette grown by width units all round, in a light colour. Drawn under the PC's
    # rider and the icons, it edges them like a sticker, so they show on any background.
    edge = f'fill="#F8FAFC" stroke="#F8FAFC" stroke-width="{width * 2:.1f}" stroke-linejoin="round"'
    return SVG_OPEN + f'<path d="{SHELL}" {edge}/><path d="{SPOILER}" {edge}/>' + "</svg>"


def phone_face_svg(paint: str = None) -> str:
    # Every expression's parts in one SVG for the phone page. Each part is marked with the moods that
    # use it; the page's CSS shows those of the face's data-mood and colours the visor, and its script
    # moves the eyes.
    parts = ["<defs>" + base_defs(paint) + _paints("blue") + "".join(_paints(v, "-" + v) for v in VISORS) + "</defs>",
             SHELL_ART, FRONT_ART]
    for name, markup in PARTS.items():
        moods = [m for m, (e, b, ex) in MOOD_PARTS.items() if name in (e, b) or name in ex]
        if not moods:
            continue
        if name in LOOKING_EYES:
            markup = f'<g class="look">{markup}</g>'
        parts.append(f'<g class="part {" ".join("m-" + m for m in moods)}">{markup}</g>')
    return SVG_OPEN.replace("<svg ", '<svg class="face-svg" ') + "".join(parts) + "</svg>"


def phone_face_css() -> str:
    # Shows the parts of the face's data-mood and paints the visor in its colours. (Changing a
    # gradient's colours in place leaves Chrome showing the old ones, so each visor has its own.)
    lines = ["  .face .part { display: none; }",
             "  .face .sheen { transition: opacity .4s ease; }",
             '  .face[data-mood="sleeping"] .sheen { opacity: 0; }']
    lines += [f'  .face[data-mood="{m}"] .m-{m} {{ display: inline; }}' for m in MOODS]
    for m in MOODS:
        visor = MOOD_VISOR[m]
        r, g, b = (int(VISORS[visor][0][i:i + 2], 16) for i in (1, 3, 5))
        strength = 0.14 if m == "sleeping" else 0.3  # the glow behind the face, in the visor's colour
        lines.append(f'  .face[data-mood="{m}"] .sheen-a {{ fill: url(#hm-sheen-{visor}); }} '
                     f'.face[data-mood="{m}"] .sheen-b {{ fill: url(#hm-low-{visor}); }} '
                     f'.face[data-mood="{m}"] .glow {{ fill: url(#hm-glow-{visor}); }} '
                     f'.face[data-mood="{m}"] .glow-line {{ stroke: url(#hm-line-{visor}); }}')
        lines.append(f'  .face[data-mood="{m}"]::before {{ background: radial-gradient(circle, rgba({r},{g},{b},{strength}) 0%, '
                     f'rgba({r},{g},{b},0) 66%); }}')
    return "\n".join(lines)


def mood_look_json() -> str:
    return json.dumps({mood: list(look) for mood, look in MOOD_LOOK.items()})
