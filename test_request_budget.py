import unittest

from request_budget import RequestBudgetExceeded, activate, consume_request, deactivate, stats


class RequestBudgetTests(unittest.TestCase):
    def tearDown(self):
        deactivate()

    def test_global_limit_is_atomic_and_reports_remaining(self):
        activate(2)
        consume_request("test A")
        self.assertEqual(stats()["remaining"], 1)
        consume_request("test B")
        self.assertTrue(stats()["exhausted"])
        with self.assertRaisesRegex(RequestBudgetExceeded, "test C"):
            consume_request("test C")
        self.assertEqual(stats()["used"], 2)


if __name__ == "__main__":
    unittest.main()
