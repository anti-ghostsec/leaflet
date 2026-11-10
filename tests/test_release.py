"""The release workflow: only main is signed, and actions are pinned.   python3 -m unittest discover tests"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, '..', '.github', 'workflows', 'release.yml'), encoding='utf-8') as f:
    WORKFLOW = f.read()


class ReleaseWorkflowTest(unittest.TestCase):
    def jobs(self):
        """Each job's text, by name (jobs are indented two spaces, their keys four)."""
        body = WORKFLOW.split('\njobs:\n', 1)[1]
        parts = re.split(r'(?m)^  ([\w-]+):\n', body)
        return dict(zip(parts[1::2], parts[2::2]))

    def test_every_job_with_the_key_runs_for_main_only(self):
        jobs = self.jobs()
        self.assertTrue(jobs)
        for name, text in jobs.items():
            if 'secrets.' not in text and 'gh release' not in text:
                continue
            with self.subTest(job=name):
                self.assertRegex(text, r"(?m)^    if: github\.ref == 'refs/heads/main'$")
                self.assertRegex(text, r'(?m)^    environment: release$')
        sign = WORKFLOW.split('- name: Sign', 1)[1].split('- name:', 1)[0]
        self.assertIn('[ "$GITHUB_REF" = refs/heads/main ] ||', sign)
        self.assertLess(sign.index('GITHUB_REF'), sign.index('printf'))      # before the key is written out

    def test_actions_are_pinned_to_a_commit(self):
        uses = re.findall(r'(?m)^\s*-?\s*uses:\s*(\S+)', WORKFLOW)
        self.assertTrue(uses)
        for action in uses:
            with self.subTest(action=action):
                self.assertRegex(action, r'^[\w.-]+/[\w./-]+@[0-9a-f]{40}$')

    def test_only_pushes_to_main_start_it(self):
        on = WORKFLOW.split('\non:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertEqual(re.sub(r'\s+', ' ', on).strip(), 'push: branches: [main] workflow_dispatch:')


if __name__ == '__main__':
    unittest.main()
