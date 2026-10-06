"""Bounded Cairo spectrum renderers, independent of capture and desktop hosting."""

import math
from itertools import pairwise

import cairo

from .wallpaper_colors import Palette
from .wallpaper_sources import STYLES


def _curve(cr, points):
    cr.move_to(*points[0])
    for a, b in pairwise(points):
        center = (a[0] + b[0]) / 2
        cr.curve_to(center, a[1], center, b[1], *b)


def _colored_curve(cr, points, palette, alpha=1):
    # Short solid-color segments avoid shading the large bounding rectangle of
    # an entire curved gradient stroke. The spectrum's 64 bins supply the colors.
    for i, (a, b) in enumerate(pairwise(points)):
        cr.set_source_rgba(*palette.at(i / max(1, len(points) - 2)), alpha)
        center = (a[0] + b[0]) / 2
        cr.move_to(*a)
        cr.curve_to(center, a[1], center, b[1], *b)
        cr.stroke()


def draw(
    cr,
    width,
    height,
    values,
    style,
    color,
    *,
    color2="#b477ff",
    color_mode="single",
    cycle_colors=False,
    color_speed="normal",
    elapsed=0,
):
    """Draw one frame; no particles, history buffers or capture processes accumulate."""
    if style not in STYLES or width <= 0 or height <= 0 or not values:
        return
    values = [min(1, max(0, v)) for v in values[:128]]
    palette = Palette(color, color2, color_mode, cycle_colors, color_speed, elapsed)
    count, size = len(values), min(width, height)
    left, right, baseline = width * 0.08, width * 0.92, height * 0.82
    step = (right - left) / count
    cr.save()
    # The backdrop stays solid even during color cycling: full-screen gradients
    # are expensive at 4K. Color and transparency are confined to the spectrum.
    cr.set_source_rgb(0.018, 0.024, 0.038)
    cr.paint()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    if style in {"bars", "blocks", "mirror"}:
        for i, value in enumerate(values):
            cr.set_source_rgb(*palette.at(i / max(1, count - 1)))
            bar_height = max(2, value * height * (0.30 if style == "mirror" else 0.52))
            x = left + i * step
            if style == "bars":
                cr.rectangle(x, baseline - bar_height, step * 0.72, bar_height)
            elif style == "mirror":
                cr.rectangle(x, height / 2 - bar_height, step * 0.72, bar_height)
                cr.fill()
                cr.set_source_rgba(*palette.at(i / max(1, count - 1)), 0.45)
                cr.rectangle(x, height / 2 + 5, step * 0.72, bar_height)
            else:
                block = height * 0.016
                for j in range(max(1, round(bar_height / (block * 1.3)))):
                    cr.rectangle(x, baseline - (j + 1) * block * 1.3, step * 0.72, block)
            cr.fill()
    elif style == "curve":
        mid = height * 0.56
        points = [(left + i * step, mid - v * height * 0.28) for i, v in enumerate(values)]
        _curve(cr, points)
        for i in range(count - 1, -1, -1):
            cr.line_to(left + i * step, mid + values[i] * height * 0.28)
        cr.close_path()
        # Keep large translucent fills solid; a multicolor outline provides the
        # palette without an expensive software gradient across a 4K surface.
        cr.set_source_rgba(*palette.at(0.5), 0.18)
        cr.fill()
        cr.set_line_width(max(2, height * 0.004))
        _colored_curve(cr, points, palette)
    elif style == "ribbons":
        energy = sum(values) / count
        for layer in range(3):
            points = []
            for i, value in enumerate(values):
                position = i / max(1, count - 1)
                envelope = math.sin(position * math.pi)
                motion = math.sin(
                    position * math.tau * (1.2 + layer * 0.35) + elapsed * 0.6 + layer * 1.7
                )
                y = height * (0.42 + layer * 0.08) + motion * envelope * height * (
                    0.06 + energy * 0.10
                )
                points.append(
                    (left + position * (right - left), y - value * envelope * height * 0.08)
                )
            _curve(cr, points)
            for x, y in reversed(points):
                cr.line_to(x, y + height * (0.012 + energy * 0.055))
            cr.close_path()
            cr.set_source_rgba(*palette.at(layer / 2), 0.18 + layer * 0.04)
            cr.fill()
            cr.set_line_width(max(2, size * 0.003))
            _colored_curve(cr, points, palette, 0.8)
    else:
        _radial(cr, width, height, values, style, palette, elapsed)
    cr.restore()


def _radial(cr, width, height, values, style, palette, elapsed):
    cx, cy, size = width / 2, height / 2, min(width, height)
    radius, count = size * 0.20, len(values)
    cr.set_line_width(max(2, size * 0.004))
    if style == "rings":
        cr.set_source_rgba(*palette.at(0.5), 0.6)
        cr.arc(cx, cy, radius, 0, math.tau)
        cr.stroke()
        for i, value in enumerate(values):
            angle = math.tau * i / count - math.pi / 2
            outer = radius + 3 + value * size * 0.22
            cr.set_source_rgb(*palette.at(i / count))
            cr.move_to(cx + math.cos(angle) * (radius + 3), cy + math.sin(angle) * (radius + 3))
            cr.line_to(cx + math.cos(angle) * outer, cy + math.sin(angle) * outer)
            cr.stroke()
    elif style == "wave_ring":
        points = []
        for i, value in enumerate(values):
            angle = math.tau * i / count - math.pi / 2
            r = radius + value * size * 0.18
            points.append((cx + math.cos(angle) * r, cy + math.sin(angle) * r))
        # The dim inner ring and a glowing outer loop follow the same spectrum.
        for scale, alpha in ((0.78, 0.30), (1, 1)):
            for i, (a, b) in enumerate(pairwise(points + points[:1])):
                cr.set_source_rgba(*palette.at(i / count), alpha)
                cr.move_to(cx + (a[0] - cx) * scale, cy + (a[1] - cy) * scale)
                cr.line_to(cx + (b[0] - cx) * scale, cy + (b[1] - cy) * scale)
                cr.stroke()
    elif style == "particles":
        for i in range(32):
            value = values[i * count // 32]
            angle = math.tau * i / 32 + elapsed * (0.16 if i % 2 else -0.12)
            r = size * (0.15 + (i % 4) * 0.047 + value * 0.09)
            dot = max(1.5, size * (0.002 + value * 0.01))
            for trail in range(3, -1, -1):
                a = angle - trail * 0.022
                cr.set_source_rgba(*palette.at(i / 31), (1 - trail * 0.22) * (0.2 + value * 0.8))
                cr.arc(
                    cx + math.cos(a) * r,
                    cy + math.sin(a) * r,
                    dot * (1 - trail * 0.18),
                    0,
                    math.tau,
                )
                cr.fill()
    elif style == "spiral":
        points = []
        for i in range(129):
            position = i / 128
            value = values[min(count - 1, int(position * count))]
            angle = position * math.tau * 2.5 + elapsed * 0.18
            r = size * (0.045 + position * 0.30 + value * 0.055)
            points.append((cx + math.cos(angle) * r, cy + math.sin(angle) * r))
        for i, (a, b) in enumerate(pairwise(points)):
            color = palette.at(i / 128)
            for thickness, alpha in ((0.012, 0.12), (0.003, 1)):
                cr.set_line_width(max(1.5, size * thickness))
                cr.set_source_rgba(*color, alpha)
                cr.move_to(*a)
                cr.line_to(*b)
                cr.stroke()
