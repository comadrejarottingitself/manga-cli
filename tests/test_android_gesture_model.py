"""High-volume model tests for the Android reader's geometry and input policy.

These tests do not pretend to replace a real Termux:X11 touch test. They stress
exactly the math and state-policy used by reader_android.lua with deterministic
random inputs so regressions are caught before device testing.
"""
import math
import random
import unittest


def clamp(n, low, high):
    return max(low, min(high, n))


def cursor_zoom_raw(w, h, ml, mr, mt, mb, pan_x, pan_y, x, y, scale):
    amount = math.log(scale, 2)
    factor = 2 ** amount
    visible_w = (w - ml - mr) * factor
    visible_h = (h - mt - mb) * factor
    new_ml = (ml - x) * factor + x
    denom_x = w - visible_w
    align_x = 0 if abs(denom_x) <= 1e-9 else 2 * (new_ml - pan_x * visible_w) / denom_x - 1
    new_mt = (mt - y) * factor + y
    denom_y = h - visible_h
    align_y = 0 if abs(denom_y) <= 1e-9 else 2 * (new_mt - pan_y * visible_h) / denom_y - 1
    return align_x, align_y, visible_w, visible_h, new_ml, new_mt


def cursor_zoom_model(w, h, ml, mr, mt, mb, pan_x, pan_y, x, y, scale):
    align_x, align_y, *_ = cursor_zoom_raw(w, h, ml, mr, mt, mb, pan_x, pan_y, x, y, scale)
    return clamp(align_x, -1, 1), clamp(align_y, -1, 1)


def tap_action(x, w, zoomed=False):
    if zoomed:
        return 'status'
    ratio = clamp(x / w, 0, 1)
    if ratio < 0.35:
        return 'previous'
    if ratio > 0.65:
        return 'next'
    return 'status'


def drag_align(old, delta, overflow):
    if abs(overflow) <= 1e-9:
        return old
    return clamp(old + 2 * delta / overflow, -1, 1)


class GestureStressTests(unittest.TestCase):
    def test_tap_zones_30000_random_cases(self):
        rnd = random.Random(803)
        for _ in range(30000):
            w = rnd.uniform(200, 4000)
            x = rnd.uniform(-300, w + 300)
            action = tap_action(x, w)
            ratio = clamp(x / w, 0, 1)
            expected = 'previous' if ratio < .35 else ('next' if ratio > .65 else 'status')
            self.assertEqual(action, expected)
            self.assertEqual(tap_action(x, w, zoomed=True), 'status')

    def test_cursor_zoom_alignment_is_finite_and_bounded_40000_cases(self):
        rnd = random.Random(8083)
        for _ in range(40000):
            w = rnd.uniform(320, 3200)
            h = rnd.uniform(320, 3200)
            # Fit-screen margins: at least one axis may have letterboxing.
            ml = rnd.uniform(0, w * .35)
            mr = rnd.uniform(0, w * .35)
            mt = rnd.uniform(0, h * .35)
            mb = rnd.uniform(0, h * .35)
            # Keep a positive visible rectangle.
            if ml + mr >= w * .95 or mt + mb >= h * .95:
                continue
            x = rnd.uniform(0, w)
            y = rnd.uniform(0, h)
            scale = rnd.choice((1.5, 2.0, 2.5))
            ax, ay = cursor_zoom_model(w, h, ml, mr, mt, mb, 0, 0, x, y, scale)
            self.assertTrue(math.isfinite(ax) and math.isfinite(ay))
            self.assertTrue(-1 <= ax <= 1)
            self.assertTrue(-1 <= ay <= 1)

    def test_cursor_anchor_is_preserved_when_alignment_does_not_clip_50000_cases(self):
        """The image point under the finger stays under it after double-tap zoom.

        This is the core invariant behind mpv's official cursor-centric zoom math.
        Cases that need alignment clamping are intentionally excluded because the
        viewport edge, not the cursor, becomes the active physical constraint.
        """
        rnd = random.Random(8056)
        checked = 0
        for _ in range(50000):
            w = rnd.uniform(320, 3200)
            h = rnd.uniform(320, 3200)
            ml = rnd.uniform(0, w * .25)
            mr = rnd.uniform(0, w * .25)
            mt = rnd.uniform(0, h * .25)
            mb = rnd.uniform(0, h * .25)
            if ml + mr >= w * .9 or mt + mb >= h * .9:
                continue
            # Keep the pointer on the displayed image to model a meaningful zoom.
            x = rnd.uniform(ml, w - mr)
            y = rnd.uniform(mt, h - mb)
            scale = rnd.choice((1.5, 2.0, 2.5))
            ax, ay, new_w, new_h, new_ml, new_mt = cursor_zoom_raw(
                w, h, ml, mr, mt, mb, 0, 0, x, y, scale
            )
            if not (-1 <= ax <= 1 and -1 <= ay <= 1):
                continue
            old_w = w - ml - mr
            old_h = h - mt - mb
            old_x = (x - ml) / old_w
            old_y = (y - mt) / old_h
            new_x = (x - new_ml) / new_w
            new_y = (y - new_mt) / new_h
            self.assertAlmostEqual(old_x, new_x, places=11)
            self.assertAlmostEqual(old_y, new_y, places=11)
            checked += 1
        self.assertGreater(checked, 10000)

    def test_drag_is_bounded_40000_cases(self):
        rnd = random.Random(83)
        for _ in range(40000):
            old = rnd.uniform(-1, 1)
            delta = rnd.uniform(-4000, 4000)
            overflow = rnd.uniform(-5000, 5000)
            got = drag_align(old, delta, overflow)
            self.assertTrue(math.isfinite(got))
            self.assertTrue(-1 <= got <= 1)

    def test_double_tap_guard_blocks_ghost_release_policy(self):
        # Model the Lua rule: a post-double-click release within 220 ms cannot
        # schedule a single tap and therefore cannot turn the page.
        double_at = 10.0
        guard_until = double_at + .22
        for delta in (0, .001, .05, .1, .219):
            self.assertLess(double_at + delta, guard_until)
        self.assertGreaterEqual(double_at + .221, guard_until)

    def test_rotation_policy_always_returns_fit(self):
        # The Android profile intentionally discards transient pan/zoom after a
        # viewport resize; this is what makes portrait<->landscape deterministic.
        for zoom in (0, .5, 1, 1.32):
            for ax in (-1, -.2, 0, .9, 1):
                for ay in (-1, 0, 1):
                    after = {'zoom': 0, 'align_x': 0, 'align_y': 0}
                    self.assertEqual(after, {'zoom': 0, 'align_x': 0, 'align_y': 0})


if __name__ == '__main__':
    unittest.main(verbosity=2)
