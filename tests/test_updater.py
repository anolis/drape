from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from drape import updater


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.remote = self.root / "origin.git"
        self.writer = self.root / "writer"
        self.client = self.root / "client"
        self.git_cmd("init", "--bare", "--initial-branch=main", str(self.remote))
        self.git_cmd("clone", str(self.remote), str(self.writer))
        self.commit(self.writer, "first")
        self.git_cmd("-C", str(self.writer), "push", "origin", "main")
        self.git_cmd("clone", str(self.remote), str(self.client))
        patch = mock.patch.object(updater, "OFFICIAL_REMOTES", {str(self.remote)})
        patch.start()
        self.addCleanup(patch.stop)

    def git_cmd(self, *args):
        proc = subprocess.run(["git", *args], text=True, capture_output=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def commit(self, checkout, text):
        (checkout / "app.txt").write_text(text)
        self.git_cmd("-C", str(checkout), "add", "app.txt")
        self.git_cmd("-C", str(checkout), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "-m", text)

    def publish(self):
        self.commit(self.writer, "new release")
        self.git_cmd("-C", str(self.writer), "push", "origin", "main")

    def test_clean_checkout_fast_forwards_to_reviewed_update(self):
        self.assertIsNone(updater.check(self.client))
        self.publish()
        update = updater.check(self.client)
        self.assertEqual(update.count, 1)
        self.assertFalse(update.blocked)
        self.assertIn("new release", update.summary)
        updater.apply(update, self.client)
        self.assertEqual((self.client / "app.txt").read_text(), "new release")
        self.assertIsNone(updater.check(self.client))

    def test_local_edit_and_untracked_file_are_preserved(self):
        self.publish()
        for file in ("app.txt", "untracked.txt"):
            with self.subTest(file=file):
                path = self.client / file
                original = path.read_text() if path.exists() else None
                path.write_text("user work")
                update = updater.check(self.client)
                self.assertIn("local changes", update.blocked)
                with self.assertRaises(updater.UpdateError):
                    updater.apply(update, self.client)
                self.assertEqual(path.read_text(), "user work")
                if original is None:
                    path.unlink()
                else:
                    path.write_text(original)

    def test_edit_after_notification_is_rechecked(self):
        self.publish()
        update = updater.check(self.client)
        (self.client / "app.txt").write_text("edited after check")
        with self.assertRaises(updater.UpdateError):
            updater.apply(update, self.client)
        self.assertEqual((self.client / "app.txt").read_text(), "edited after check")

    def test_local_commit_blocks_divergent_update(self):
        self.publish()
        self.commit(self.client, "local work")
        update = updater.check(self.client)
        self.assertIn("local commits", update.blocked)
        with self.assertRaises(updater.UpdateError):
            updater.apply(update, self.client)

    def test_branch_and_remote_changes_block_updates(self):
        self.publish()
        update = updater.check(self.client)
        self.git_cmd("-C", str(self.client), "checkout", "-b", "work")
        with self.assertRaises(updater.UpdateError):
            updater.apply(update, self.client)
        self.git_cmd("-C", str(self.client), "checkout", "main")
        self.git_cmd("-C", str(self.client), "remote", "set-url", "origin", "https://example.invalid/other.git")
        with self.assertRaises(updater.UpdateError):
            updater.check(self.client)


if __name__ == "__main__":
    unittest.main()
