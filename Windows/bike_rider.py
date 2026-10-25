# The rider: the helmet (helmet_face) on a blacked-out supersport bike, for the buddy that rides around
# the PC. The bike and the rider's body are one SVG facing right, drawn with real proportions (a
# little big in the head, to be cute); the wheels and the head are drawn on top by the PC, so the
# wheels can turn and the head can show every mood.
import random

import paint_jobs

BOX = (300, 200)  # the drawing's size; the ground is at GROUND_Y
GROUND_Y = 196
REAR_WHEEL, FRONT_WHEEL, WHEEL_R = (64, 158), (240, 158), 38
HEAD, HEAD_SIZE = (197, 41), 88  # the middle of the helmet's 200-unit drawing, and how big it is drawn
GROUND = ((REAR_WHEEL[0] + FRONT_WHEEL[0]) / 2, GROUND_Y)  # the point on the ground between the wheels
EXHAUST = (102, 136)  # the muffler's open end, where its smoke comes out
TAIL_LIGHT = (43, 44)  # the brake light, which glows when it stops
HEADLIGHT = (262, 76)  # the headlights, which flash hello
SVG_OPEN = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 200">'

EDGE = "#3E4555"


def _flakes():
    # A few metal flakes in the paint, like the helmet's.
    rnd = random.Random(7)
    spots = [(rnd.uniform(160, 214), rnd.uniform(50, 62)) for _ in range(10)]
    spots += [(rnd.uniform(200, 262), rnd.uniform(66, 90)) for _ in range(8)]
    spots += [(rnd.uniform(52, 104), rnd.uniform(46, 62)) for _ in range(6)]
    return "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{rnd.uniform(0.4, 0.9):.2f}" fill="#FFFFFF" '
                   f'opacity="{rnd.uniform(0.3, 0.8):.2f}"/>' for x, y in spots)


BIKE = (
    '<defs>'
    '<!--BODYWORK-->'
    # the swingarm, the frame and the muffler: black in every paint job
    '<linearGradient id="rd-dark" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#363B47"/><stop offset="0.3" stop-color="#15171D"/><stop offset="1" stop-color="#040405"/>'
    '</linearGradient>'
    '<linearGradient id="rd-matte" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#2A2D35"/><stop offset="1" stop-color="#121419"/></linearGradient>'
    '<linearGradient id="rd-leather" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#3A3F4B"/><stop offset="0.5" stop-color="#1A1D24"/><stop offset="1" stop-color="#0C0D11"/>'
    '</linearGradient>'
    '<linearGradient id="rd-screen" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#9FB1E0" stop-opacity="0.7"/><stop offset="0.6" stop-color="#2A3552" stop-opacity="0.85"/>'
    '<stop offset="1" stop-color="#121829" stop-opacity="0.95"/></linearGradient>'
    '<linearGradient id="rd-metal" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#D5DCE6"/><stop offset="1" stop-color="#6B7385"/></linearGradient>'
    '<linearGradient id="rd-engine" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#3A3F4A"/><stop offset="1" stop-color="#15171C"/></linearGradient>'
    '<radialGradient id="rd-lamp" cx="0.5" cy="0.5" r="0.5">'
    '<stop offset="0" stop-color="#E0F7FF" stop-opacity="0.9"/><stop offset="1" stop-color="#7DD3FC" stop-opacity="0"/>'
    '</radialGradient>'
    '<radialGradient id="rd-red" cx="0.5" cy="0.5" r="0.5">'
    '<stop offset="0" stop-color="#FF5470" stop-opacity="0.85"/><stop offset="1" stop-color="#FF2D55" stop-opacity="0"/>'
    '</radialGradient>'
    '</defs>'
    # behind: the chain, the swingarm, the frame and the engine with its fins
    '<path d="M140 125 L64 146 M142 139 L64 170" stroke="#2A2D35" stroke-width="2.2" stroke-linecap="round"/>'
    f'<path d="M133 119 C138 121 141 126 141 132 L70 167 C64 168 58 162 59 155 Z" fill="url(#rd-dark)" stroke="{EDGE}" '
    'stroke-width="1" stroke-linejoin="round"/>'
    '<path d="M66 151 L136 122" stroke="#FFFFFF" stroke-opacity="0.22" stroke-width="1.4" stroke-linecap="round"/>'
    f'<path d="M206 62 L218 70 L148 130 L132 121 Z" fill="url(#rd-dark)" stroke="{EDGE}" stroke-width="1"/>'
    f'<path d="M150 98 L208 92 L214 132 L196 152 L156 150 Z" fill="url(#rd-engine)" stroke="{EDGE}" stroke-width="1"/>'
    '<path d="M162 104 L204 99 M163 110 L205 105 M164 116 L206 111 M165 122 L207 117" stroke="#0B0C10" stroke-width="2"/>'
    '<ellipse cx="174" cy="138" rx="13" ry="9" fill="#2E333F" stroke="#5B6272" stroke-width="1"/>'
    '<ellipse cx="172" cy="136" rx="6" ry="3" fill="#FFFFFF" opacity="0.12"/>'
    # the muffler: a short angular canister under the tail, its open end at EXHAUST, and its link pipe
    '<path d="M166 150 C176 156 188 160 200 156" fill="none" stroke="#1A1D24" stroke-width="7" stroke-linecap="round"/>'
    '<g transform="translate(132 144) rotate(15)">'
    f'<path d="M-30 -8 L25 -9 C30 -9 32 -5 32 0 C32 5 30 9 25 9 L-30 8 Z" fill="url(#rd-dark)" stroke="{EDGE}" stroke-width="1.2"/>'
    '<path d="M-24 -5.5 L22 -6.5" stroke="#FFFFFF" stroke-opacity="0.3" stroke-width="1.8" stroke-linecap="round"/>'
    '<path d="M-16 3 L18 2" stroke="#000000" stroke-opacity="0.4" stroke-width="3" stroke-linecap="round"/>'
    '<ellipse cx="-31" cy="0" rx="3.2" ry="8" fill="url(#rd-metal)"/><ellipse cx="-31.5" cy="0" rx="2" ry="5.2" fill="#050608"/>'
    '</g>'
    # the belly pan, the big side fairing with its cut-out, and the upper cowl down to the beak
    f'<path d="M150 150 C168 160 196 160 216 150 L224 130 L200 124 C188 134 170 142 152 146 Z" fill="url(#rd-gloss-side)" '
    f'stroke="{EDGE}" stroke-width="1" stroke-linejoin="round"/>'
    f'<path d="M180 70 L254 80 C262 84 267 90 266 97 L238 120 L224 132 L201 126 L188 104 L168 100 Z" fill="url(#rd-gloss-side)" '
    f'stroke="{EDGE}" stroke-width="1" stroke-linejoin="round"/>'
    '<path d="M196 104 L230 103 L218 122 L205 120 Z" fill="#07080A" stroke="#2E333F" stroke-width="1"/>'
    '<path d="M200 108 L224 107 M203 113 L221 112" stroke="#1E2129" stroke-width="1.6"/>'
    '<path d="M182 76 L252 85 L262 92" fill="none" stroke="#FFFFFF" stroke-opacity="0.28" stroke-width="1.4" stroke-linecap="round"/>'
    '<path d="M234 98 L246 98 L224 120 L218 118 Z" fill="#2A2F3A" opacity="0.8"/>'
    f'<path d="M216 54 C238 56 258 62 276 74 L281 82 L270 90 L256 85 L188 72 L196 58 Z" fill="url(#rd-gloss)" '
    f'stroke="{EDGE}" stroke-width="1" stroke-linejoin="round"/>'
    '<path d="M222 59 C240 61 256 66 270 75" fill="none" stroke="#FFFFFF" stroke-opacity="0.38" stroke-width="1.5" stroke-linecap="round"/>'
    '<path d="M270 86 L280 84 L278 91 L270 91 Z" fill="#050608" stroke="#2E333F" stroke-width="0.8"/>'
    '<ellipse cx="262" cy="77" rx="16" ry="7" fill="url(#rd-lamp)"/>'
    '<path d="M249 72 L273 78 L269 82 L247 77 Z" fill="#EAF8FF"/>'
    '<path d="M252 74 L270 78.5" stroke="#7DD3FC" stroke-width="0.8"/>'
    # the windscreen, smoked like the visor, and a mirror
    '<path d="M222 56 L232 38 C238 33 245 31 252 34 C258 44 264 56 269 67 L246 61 Z" fill="url(#rd-screen)" '
    'stroke="#5B6272" stroke-width="0.9" stroke-linejoin="round"/>'
    '<path d="M234 40 C240 36 246 35 251 37" fill="none" stroke="#FFFFFF" stroke-opacity="0.6" stroke-width="1.3" stroke-linecap="round"/>'
    '<path d="M240 60 L236 52 L231 50" fill="none" stroke="#14161B" stroke-width="2.2" stroke-linecap="round"/>'
    '<path d="M224 46 L236 48 L234 53 L223 51 Z" fill="#14161B" stroke="#4A5160" stroke-width="0.8"/>'
    # the tank, the seat, and the sharp tail sweeping up to its light, with the shadowed under-tail
    '<path d="M148 58 C166 46 196 42 222 52 L216 72 L154 76 Z" fill="url(#rd-gloss)" stroke="#3E4555" stroke-width="1" '
    'stroke-linejoin="round"/>'
    '<path d="M160 54 C176 47 196 46 214 51" fill="none" stroke="#FFFFFF" stroke-opacity="0.5" stroke-width="2.2" stroke-linecap="round"/>'
    '<path d="M108 62 C122 58 138 57 150 59 L148 67 C134 67 120 69 110 71 Z" fill="url(#rd-matte)" stroke="#0B0C10" stroke-width="0.9"/>'
    '<path d="M100 76 L128 94 L134 114 L116 120 L102 100 Z" fill="#0B0C10"/>'
    '<path d="M60 116 C66 104 80 96 96 96 L98 102 C84 102 72 108 66 118 Z" fill="#14161B" stroke="#3E4555" stroke-width="0.8"/>'
    '<path d="M117 86 L127 116" stroke="#3B4150" stroke-width="5" stroke-linecap="round"/>'
    '<path d="M117 86 L127 116" stroke="#8A92A3" stroke-width="5" stroke-dasharray="1.2 1.8"/>'
    f'<path d="M112 62 C94 56 70 48 40 41 L42 50 C56 58 76 70 100 86 L114 76 Z" fill="url(#rd-gloss)" stroke="{EDGE}" stroke-width="1" '
    'stroke-linejoin="round"/>'
    '<path d="M44 52 C60 60 80 72 98 84" fill="none" stroke="#000000" stroke-opacity="0.45" stroke-width="4" stroke-linecap="round"/>'
    '<path d="M44 42.5 C66 47.5 88 54 108 61" fill="none" stroke="#FFFFFF" stroke-opacity="0.42" stroke-width="1.3" stroke-linecap="round"/>'
    '<path d="M82 52 C90 50 98 52 104 56 L102 60 C94 57 88 56 82 57 Z" fill="#202329"/>'
    '<ellipse cx="44" cy="45" rx="7" ry="5" fill="url(#rd-red)"/>'
    '<path d="M40 41 L49 42.5 L47.5 47.5 L42 48 Z" fill="#FF3355"/>'
    # the front: mudguard over the tyre, the upside-down fork with its shiny tube, the brake caliper,
    # and the clip-on bar
    f'<path d="M220 120 C228 112 246 109 262 115 L258 121 C246 117 233 119 225 125 Z" fill="url(#rd-gloss)" stroke="{EDGE}" stroke-width="0.9"/>'
    '<path d="M213 66 L229 118" stroke="#1A1D24" stroke-width="9" stroke-linecap="round"/>'
    '<path d="M212 68 L226 112" stroke="#FFFFFF" stroke-opacity="0.18" stroke-width="2" stroke-linecap="round"/>'
    '<path d="M229 118 L240 158" stroke="#2A2E38" stroke-width="6.5" stroke-linecap="round"/>'
    '<path d="M229 121 L236 146" stroke="url(#rd-metal)" stroke-width="2" stroke-linecap="round"/>'
    '<path d="M224 138 L232 136 L236 152 L228 155 Z" fill="#14161B" stroke="#4A5160" stroke-width="0.8"/>'
    '<path d="M214 68 L203 72" stroke="#14161B" stroke-width="4" stroke-linecap="round"/>'
    + _flakes() +
    # the rider in black leathers with dark grey panels: the leg tucked against the tank with its knee
    # slider, the boot on the peg, the back flat over the bike, and an arm reaching to the bar
    '<path d="M120 125 L136 123" stroke="url(#rd-metal)" stroke-width="3" stroke-linecap="round"/>'
    '<path d="M124 118 L140 116 L138 128 Z" fill="#2A2E38"/>'
    '<path d="M118 62 C140 60 164 70 182 86 C186 92 182 99 175 99 C158 92 138 82 116 78 Z" fill="url(#rd-leather)" '
    'stroke="#5B6272" stroke-width="1"/>'
    '<path d="M122 63 C142 62 162 71 178 84" fill="none" stroke="#7C8496" stroke-opacity="0.6" stroke-width="1.4" stroke-linecap="round"/>'
    '<path d="M180 88 C178 100 162 112 140 120 L134 112 C150 106 162 98 170 90 Z" fill="url(#rd-leather)" stroke="#5B6272" stroke-width="1"/>'
    '<ellipse cx="180" cy="94" rx="5" ry="6.5" fill="url(#rd-metal)" stroke="#2E333F" stroke-width="0.8" transform="rotate(-25 180 94)"/>'
    '<path d="M116 111 C124 107 138 107 144 112 L145 124 L118 126 C113 123 113 115 116 111 Z" fill="#0C0D11" stroke="#5B6272" stroke-width="1"/>'
    '<path d="M116 122 L145 121" stroke="#2E333F" stroke-width="2"/>'
    '<rect x="111" y="114" width="7" height="5" rx="2" fill="url(#rd-metal)"/>'
    '<path d="M112 70 C112 56 124 46 144 43 C162 40 180 44 191 52 L186 66 C168 70 140 72 116 74 Z" fill="url(#rd-leather)" '
    'stroke="#5B6272" stroke-width="1"/>'
    '<path d="M118 60 C124 50 138 45 156 44" fill="none" stroke="#9AA2B2" stroke-opacity="0.55" stroke-width="1.6" stroke-linecap="round"/>'
    '<path d="M122 68 C134 58 152 53 172 53" fill="none" stroke="#3B4150" stroke-width="3.4" stroke-linecap="round"/>'
    '<path d="M182 52 Q195 58 203 69" fill="none" stroke="#5B6272" stroke-width="13.5" stroke-linecap="round"/>'
    '<path d="M182 52 Q195 58 203 69" fill="none" stroke="url(#rd-leather)" stroke-width="11.5" stroke-linecap="round"/>'
    '<path d="M184 49 Q196 54 204 63" fill="none" stroke="#7C8496" stroke-opacity="0.5" stroke-width="1.3" stroke-linecap="round"/>'
    '<ellipse cx="205" cy="70" rx="6.5" ry="5.5" fill="#101217" stroke="#5B6272" stroke-width="1"/>'
    '<path d="M201 67 Q205 65 209 68" fill="none" stroke="#7C8496" stroke-width="1.4" stroke-linecap="round"/>'
)


def bike_svg(paint: str = None) -> str:
    # The bike, its bodywork in a paint job's colours (see paint_jobs.py).
    job = paint_jobs.paint(paint)
    bodywork = (f'<linearGradient id="rd-gloss" x1="0" y1="0" x2="0" y2="1">{paint_jobs.stops(job["gloss"], (0, 0.3, 1))}'
                '</linearGradient>'
                f'<linearGradient id="rd-gloss-side" x1="0" y1="0" x2="1" y2="1">{paint_jobs.stops(job["side"], (0, 0.45, 1))}'
                '</linearGradient>')
    return SVG_OPEN + BIKE.replace("<!--BODYWORK-->", bodywork) + "</svg>"
