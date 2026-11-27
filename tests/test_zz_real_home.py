"""The suite's guard (lc-oh7l), named to run last in `unittest discover`: nothing testhome.py watches in the real home
changed while the tests ran (it takes the listing before the first test module loads)."""
import testhome  # noqa: I001

import sys
import unittest


class RealHomeUntouched(unittest.TestCase):
    def test_nothing_real_changed(self):
        changed = testhome.check()
        if changed:
            testhome.reported.update(changed)  # said here, so the check at exit stays quiet about these
            if testhome.WARN_ONLY:
                print(testhome.warning(changed), file=sys.stderr)
                self.skipTest(f"{testhome.OPT_OUT}=warn: the real home changed (printed above)")
            self.fail(testhome.report(changed))
