import unittest

from drape import animation


class FakePlayer:
    def __init__(self, running=True):
        self.running = running
        self.updates = 0

    def update(self):
        self.updates += 1


class GovernorTest(unittest.TestCase):
    def make(self, cpu_per_second, mode="auto", busy=lambda: False):
        t = {"cpu": 0.0, "now": 0.0}

        def advance():
            t["now"] += 1
            t["cpu"] += cpu_per_second / 100

        gov = animation.Governor(
            lambda: mode, busy=busy, sampler=lambda: t["cpu"], clock=lambda: t["now"]
        )
        gov.last = (0.0, 0.0)
        switched = []
        gov.on_switch = switched.append
        return gov, advance, switched

    def run_seconds(self, gov, advance, n):
        for _ in range(n):
            advance()
            if not gov.measure():
                break

    def test_heavy_animations_switch_to_hover(self):
        gov, advance, switched = self.make(18)
        p = FakePlayer()
        gov.players.add(p)
        self.run_seconds(gov, advance, 10)
        self.assertTrue(gov.switched)
        self.assertTrue(gov.hover_only())
        self.assertEqual(len(switched), 1)
        self.assertAlmostEqual(switched[0], 18, delta=0.5)
        self.assertEqual(p.updates, 1)

    def test_light_animations_keep_playing(self):
        gov, advance, switched = self.make(6)
        p = FakePlayer()  # players are held weakly: keep a reference
        gov.players.add(p)
        self.run_seconds(gov, advance, 30)
        self.assertEqual(len(gov.samples), animation.WINDOW)  # it really measured
        self.assertFalse(gov.switched)
        self.assertFalse(gov.hover_only())

    def test_busy_time_is_not_blamed_on_animations(self):
        busy = {"on": True}
        gov, advance, _ = self.make(40, busy=lambda: busy["on"])
        p = FakePlayer()
        gov.players.add(p)
        self.run_seconds(gov, advance, 10)  # page loading: heavy, but not counted
        self.assertFalse(gov.switched)
        busy["on"] = False
        self.run_seconds(gov, advance, 10)  # same load once the page settles: that's the animations
        self.assertTrue(gov.switched)

    def test_nothing_playing_means_nothing_measured(self):
        gov, advance, _ = self.make(50)
        p = FakePlayer(running=False)
        gov.players.add(p)
        self.run_seconds(gov, advance, 10)
        self.assertFalse(gov.switched)

    def test_modes_override_auto(self):
        self.assertTrue(animation.Governor(lambda: "hover").hover_only())
        gov = animation.Governor(lambda: "always")
        gov.switched = True
        self.assertFalse(gov.hover_only())


class ScheduleTest(unittest.TestCase):
    def test_frame_rate_capped_but_duration_kept(self):
        # 40 frames of 20 ms (50 fps, 800 ms) capped at 15 fps -> 12 ticks of ~66.7 ms
        step, ticks = animation.schedule([20] * 40, max_fps=15)
        self.assertAlmostEqual(step, 1000 / 15)
        self.assertEqual(len(ticks), 12)
        self.assertEqual(ticks, sorted(ticks))  # frames stay in order

    def test_long_frames_last_longer(self):
        step, ticks = animation.schedule([100, 300])
        self.assertEqual((step, ticks), (100, [0, 1, 1, 1]))

    def test_builds_a_gtk_animation(self):
        from gi.repository import GdkPixbuf

        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 4, 4)
        anim = animation.build_animation([(pb, 50), (pb, 50)], max_fps=15)
        self.assertFalse(anim.is_static_image())


if __name__ == "__main__":
    unittest.main()
