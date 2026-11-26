"""The suite's guard (lc-oh7l), named to run last in `unittest discover`: nothing under the real lane-control folders or
~/.agents changed while the tests ran (testhome.py takes the listing before the first test module loads)."""
import testhome  # noqa: I001

import unittest


class RealHomeUntouched(unittest.TestCase):
    def test_nothing_real_changed(self):
        changed = testhome.check()
        if changed:
            testhome.reported.update(changed)  # said here, so the check at exit stays quiet about these
            self.fail(testhome.report(changed))
