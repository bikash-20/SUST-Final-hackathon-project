import unittest

from app.domain.coordination.state_machine import ALLOWED_TRANSITIONS


class CoordinationFSMTests(unittest.TestCase):
    def test_escalation_paths_and_resolution(self) -> None:
        self.assertIn("ESCALATED", ALLOWED_TRANSITIONS["PENDING"])
        self.assertIn("ESCALATED", ALLOWED_TRANSITIONS["ACKNOWLEDGED"])
        self.assertEqual(ALLOWED_TRANSITIONS["ESCALATED"], {"RESOLVED"})


if __name__ == "__main__":
    unittest.main()
