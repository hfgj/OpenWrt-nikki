"""Deployment guard integration tests with disposable Git remotes."""
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check-feed-source.sh"

class FeedSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.local = self.root / "checkout"
        self.remote = self.root / "remote.git"
        self.local.mkdir()
        self.git("init", "-b", "hfgj")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.commit()
        self.sha = self.git("rev-parse", "HEAD")
        subprocess.run(["git", "clone", "--bare", str(self.local), str(self.remote)], check=True, capture_output=True)
        self.git("remote", "add", "origin", str(self.remote))

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.local), *args], text=True, stderr=subprocess.DEVNULL).strip()

    def commit(self):
        self.git("-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-m", "fixture")

    def guard(self, sha=None):
        return subprocess.run(["bash", str(SCRIPT), sha or self.sha], cwd=self.local, capture_output=True, text=True)

    def test_matching_source_passes_without_moving_refs(self):
        self.assertEqual(self.guard().returncode, 0)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.sha)
        self.assertEqual(self.git("ls-remote", "origin", "refs/heads/hfgj").split()[0], self.sha)

    def test_new_remote_commit_blocks_old_checkout(self):
        self.commit()
        self.git("-c", "core.hooksPath=/dev/null", "push", "origin", "hfgj")
        self.git("checkout", "--detach", self.sha)
        result = self.guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outdated", result.stderr)

    def test_checkout_mismatch_blocks_publication(self):
        result = self.guard("0" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Checkout differs", result.stderr)

    def test_missing_branch_blocks_publication(self):
        subprocess.run(["git", "--git-dir", str(self.remote), "update-ref", "-d", "refs/heads/hfgj"], check=True, capture_output=True)
        self.assertNotEqual(self.guard().returncode, 0)

    def test_unreadable_remote_blocks_publication(self):
        self.git("remote", "set-url", "origin", str(self.root / "missing.git"))
        self.assertNotEqual(self.guard().returncode, 0)

    def test_invalid_sha_blocks_publication(self):
        result = self.guard("invalid")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid locked", result.stderr)
