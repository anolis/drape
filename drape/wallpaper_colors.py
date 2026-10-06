"""Spectrum palettes, custom gradients and smooth color cycling."""

import colorsys
import math

PALETTES = {
    "aurora": ("#63ffd3", "#48a6ff", "#a47bff", "#ff73cd"),
    "sunset": ("#ffbe57", "#ff7e70", "#ed54a6", "#9363e8"),
    "fire": ("#ff3d46", "#ff851b", "#ffcf45", "#fff2a3"),
    "ocean": ("#448bff", "#36c5ef", "#62ecd0", "#b6fff0"),
}
RATES = {"slow": 1 / 60, "normal": 1 / 24, "fast": 1 / 8}


def rgb(color):
    return tuple(int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))


class Palette:
    def __init__(
        self, color, color2="#b477ff", mode="single", cycle=False, speed="normal", elapsed=0
    ):
        self.mode = mode
        self.base = rgb(color)
        self.stops = (
            tuple(rgb(c) for c in PALETTES[mode]) if mode in PALETTES else (self.base, rgb(color2))
        )
        self.phase = elapsed * RATES[speed] if cycle else 0

    def at(self, position):
        if self.mode == "rainbow":
            return colorsys.hsv_to_rgb((position * 0.85 + self.phase) % 1, 0.72, 1)
        if self.mode == "single":
            hue, saturation, value = colorsys.rgb_to_hsv(*self.base)
            return colorsys.hsv_to_rgb((hue + self.phase) % 1, saturation, value)
        # Oscillation gives seamless cycling without a jump between end colors.
        position = (1 - math.cos(math.tau * (position * 0.5 + self.phase))) / 2
        scaled = position * (len(self.stops) - 1)
        index = min(len(self.stops) - 2, int(scaled))
        mix = scaled - index
        return tuple(a + (b - a) * mix for a, b in zip(self.stops[index], self.stops[index + 1]))
