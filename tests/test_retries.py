import unittest
from unittest import mock

from drape.ui import retries
from drape.ui.retries import RetryQueue


class RetryQueueTest(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.timers = []  # (ms, callback, host)
        self.submitted = []
        patches = [
            mock.patch.object(retries.time, "monotonic", side_effect=lambda: self.now),
            mock.patch.object(
                retries.GLib,
                "timeout_add",
                side_effect=lambda ms, fn, host: self.timers.append((ms, fn, host)) or len(self.timers),
            ),
            mock.patch.object(retries.GLib, "source_remove"),
            mock.patch.object(retries.GLib, "idle_add", side_effect=lambda fn, *a: fn(*a)),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.queue = RetryQueue(self.submitted.append)

    def fire(self, advance):
        """Advance the clock and fire the most recent timer."""
        self.now += advance
        ms, fn, host = self.timers.pop()
        fn(host)

    def test_one_job_at_a_time_per_host_after_cooldown(self):
        ran = []
        for name in "abc":
            self.queue.add("files", name, lambda name=name: ran.append(name), 60)
        self.assertEqual(len(self.timers), 1)  # one timer for the host, not one per job
        self.assertEqual(self.timers[0][0], 60_000)
        self.fire(60)
        self.assertEqual(len(self.submitted), 1)
        self.assertEqual(self.timers, [])  # nothing else starts while a job runs
        self.submitted.pop()()
        self.assertEqual(ran, ["a"])
        self.assertEqual(self.timers[-1][0], RetryQueue.SPACING * 1000)
        self.fire(RetryQueue.SPACING)
        self.submitted.pop()()
        self.fire(RetryQueue.SPACING)
        self.submitted.pop()()
        self.assertEqual(ran, ["a", "b", "c"])
        self.assertEqual(self.queue.pending(), [])

    def test_hosts_cool_down_independently(self):
        self.queue.add("files", "a", mock.Mock(), 60)
        self.queue.add("api", "b", mock.Mock(), 5)
        self.assertEqual(sorted(ms for ms, _, _ in self.timers), [5_000, 60_000])

    def test_job_limited_again_pushes_back_the_whole_host(self):
        def limited_again():
            self.queue.add("files", "a", limited_again, 120)

        self.queue.add("files", "a", limited_again, 60)
        self.queue.add("files", "b", mock.Mock(), 60)
        self.fire(60)
        self.submitted.pop()()
        self.assertEqual(self.queue.pending("files"), ["b", "a"])
        self.assertEqual(self.timers[-1][0], 120_000)

    def test_cooldown_extended_while_timer_waits_rearms_instead_of_running(self):
        job = mock.Mock()
        self.queue.add("files", "a", job, 10)
        self.queue.add("files", "b", job, 50)
        self.fire(10)
        self.assertEqual(self.submitted, [])
        self.assertEqual(self.timers[-1][0], 40_000)

    def test_same_key_replaces_waiting_job_in_place(self):
        old, new = mock.Mock(), mock.Mock()
        self.queue.add("files", "a", old, 10)
        self.queue.add("files", "b", mock.Mock(), 10)
        self.queue.add("files", "a", new, 10)
        self.assertEqual(self.queue.pending("files"), ["a", "b"])
        self.fire(10)
        self.submitted.pop()()
        new.assert_called_once()
        old.assert_not_called()

    def test_cancel_removes_waiting_jobs_and_idle_host(self):
        job = mock.Mock()
        self.queue.add("files", ("scan", "page", 1), job, 10)
        self.queue.cancel(lambda key: key[0] == "scan")
        self.assertEqual(self.queue.pending(), [])
        retries.GLib.source_remove.assert_called_once()

    def test_failing_job_still_releases_the_next(self):
        def broken():
            raise RuntimeError("boom")

        self.queue.add("files", "a", broken, 1)
        self.queue.add("files", "b", mock.Mock(), 1)
        self.fire(1)
        with self.assertRaises(RuntimeError):
            self.submitted.pop()()
        self.assertEqual(len(self.timers), 1)


if __name__ == "__main__":
    unittest.main()
