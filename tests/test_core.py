import json
import os
import tempfile
import unittest

from tower.core import TowerControl


class TowerControlTest(unittest.TestCase):
    def setUp(self):
        self.game = TowerControl()

    def _archive_path(self):
        handle, path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    # 1. Same-track conflict detection must include the boundary moment.
    def test_same_track_conflict_includes_boundary_moment(self):
        self.game.add_flight("F1", "T1", "A")
        self.game.tick(3)
        self.assertEqual(self.game.land("F1", "R1"), "landing")
        self.assertEqual(self.game.add_flight("F2", "T1", "A"), "added")
        self.assertIn(("F1", "F2"), self.game.conflicts("A"))

        other = TowerControl()
        other.add_flight("G1", "T1", "A")
        other.tick(3)
        other.land("G1", "R1")
        other.tick(1)
        other.add_flight("G2", "T1", "A")
        self.assertNotIn(("G1", "G2"), other.conflicts("A"))

    # 2. A landed flight must release the runway.
    def test_landed_flight_releases_runway(self):
        self.game.add_flight("F1", "T1", "A")
        self.game.add_flight("F2", "T2", "A")
        self.assertEqual(self.game.land("F1", "R1"), "landing")
        self.assertEqual(self.game.runways["R1"], "F1")
        self.game.tick()
        self.assertEqual(self.game.flights["F1"]["status"], "landed")
        self.assertIsNone(self.game.runways["R1"])
        self.assertEqual(self.game.land("F1", "R1"), "already-landed")
        self.assertIsNone(self.game.runways["R1"])
        self.assertEqual(self.game.land("F2", "R1"), "landing")
        self.assertEqual(self.game.runways["R1"], "F2")

    # 3. A diverted flight must not spawn duplicate alternates.
    def test_diverted_flight_is_generated_once(self):
        self.game.add_flight("F1", "T1", "A")
        first = self.game.divert("F1", "B")
        self.assertTrue(first.startswith("F1-ALT"))
        self.assertEqual(self.game.divert("F1", "B"), "duplicate")
        alternates = [fid for fid in self.game.flights if fid.startswith("F1-ALT")]
        self.assertEqual(alternates, [first])
        self.assertEqual(self.game.sectors["B"], [first])
        self.assertEqual(self.game.broadcasts.count("divert F1 -> B"), 1)

    # 4. Wind updates must change the departure window.
    def test_wind_update_affects_departure_window(self):
        self.assertIsNotNone(self.game.departure_window())
        self.assertEqual(self.game.set_wind("E"), "wind-E")
        self.assertIsNone(self.game.departure_window())
        self.assertFalse(self.game.can_depart())
        self.game.add_flight("F1", "T1", "A")
        self.assertEqual(self.game.release("F1", "AW1"), "window-closed")
        self.assertEqual(self.game.set_wind("S"), "wind-S")
        self.assertTrue(self.game.can_depart())
        self.assertEqual(self.game.release("F1", "AW1"), "released")

    # 5. A flight with zero fuel must not be delayed.
    def test_zero_fuel_flight_cannot_be_delayed(self):
        self.game.add_flight("F1", "T1", "A", fuel=0)
        self.assertEqual(self.game.delay("F1", 1), "no-fuel")
        self.assertEqual(self.game.flights["F1"]["fuel"], 0)
        self.game.tick(2)
        self.assertEqual(self.game.flights["F1"]["fuel"], 0)
        self.assertEqual(self.game.delay("F1", 1), "no-fuel")

        self.game.add_flight("F2", "T2", "A", fuel=3)
        self.assertEqual(self.game.delay("F2", 2), "delayed")
        self.assertEqual(self.game.flights["F2"]["fuel"], 1)
        self.assertEqual(self.game.delay("F2", 2), "no-fuel")
        self.assertEqual(self.game.flights["F2"]["fuel"], 1)

    # 6. Cancelling a release must free the airway.
    def test_cancel_after_release_frees_airway(self):
        self.game.add_flight("F1", "T1", "A")
        self.game.add_flight("F2", "T2", "A")
        self.assertEqual(self.game.release("F1", "AW1"), "released")
        self.assertEqual(self.game.release("F2", "AW1"), "route-busy")
        self.assertEqual(self.game.cancel("F1"), "cancelled")
        self.assertNotIn("AW1", self.game.airways)
        self.assertNotIn("F1", self.game.queue)
        self.assertEqual(self.game.cancel("F1"), "cancelled")
        self.assertNotIn("AW1", self.game.airways)
        self.assertEqual(self.game.release("F2", "AW1"), "released")

    # 7. Priority ordering must be stable for equal priorities.
    def test_priority_ordering_is_stable(self):
        self.game.add_flight("F1", "T1", "A", priority=1)
        self.game.add_flight("F2", "T2", "A", priority=2)
        self.game.add_flight("F3", "T3", "A", priority=1)
        self.game.release("F1", "AW1")
        self.game.release("F2", "AW2")
        self.game.release("F3", "AW3")
        first = self.game.departure_order()
        self.assertEqual(first, ["F2", "F1", "F3"])
        self.assertEqual(self.game.departure_order(), first)
        self.assertEqual(self.game.queue, ["F1", "F2", "F3"])

    # 8. Loading an archive must not rebroadcast instructions.
    def test_load_does_not_rebroadcast_instructions(self):
        self.game.add_flight("F1", "T1", "A")
        self.game.broadcast("cleared F1")
        self.game.divert("F1", "B")
        path = self._archive_path()
        self.assertEqual(self.game.save(path), "saved")
        before = list(self.game.broadcasts)

        restored = TowerControl()
        self.assertEqual(restored.load(path), "loaded")
        self.assertEqual(restored.broadcasts, before)
        self.assertEqual(restored.load(path), "loaded")
        self.assertEqual(restored.broadcasts, before)
        self.assertEqual(len(restored.broadcasts), len(set(restored.broadcasts)))

    # 9. Safety settlement must check every sector, not just the current one.
    def test_settlement_checks_all_sectors(self):
        self.game.add_flight("F1", "T1", "B")
        self.game.add_flight("F2", "T1", "B")
        self.game.add_flight("F3", "T3", "A")
        self.assertEqual(self.game.current_sector, "A")
        self.assertEqual(self.game.conflicts("A"), [])
        self.assertEqual(self.game.settle(), [("F1", "F2")])
        self.assertEqual(self.game.settle(), [("F1", "F2")])

    # Tracks are idempotent: the same flight id cannot be registered twice.
    def test_duplicate_flight_id_is_rejected(self):
        self.assertEqual(self.game.add_flight("F1", "T1", "A"), "added")
        self.assertEqual(self.game.add_flight("F1", "T2", "B"), "duplicate")
        self.assertEqual(self.game.sectors["A"], ["F1"])
        self.assertEqual(self.game.sectors["B"], [])
        self.assertEqual(self.game.flights["F1"]["track"], "T1")

    # JSON archive round-trips deterministically with the fixed seed.
    def test_save_load_roundtrip_is_deterministic(self):
        self.game.add_flight("F1", "T1", "A", fuel=4, priority=2)
        self.game.tick(2)
        path = self._archive_path()
        self.game.save(path)
        expected = self.game.divert("F1", "C")

        restored = TowerControl()
        restored.load(path)
        self.assertEqual(restored.divert("F1", "C"), expected)
        with open(path) as handle:
            archived = json.load(handle)
        self.assertEqual(archived["seed"], restored.seed)
        self.assertEqual(archived["time"], 2)

    # Terminal commands stay available over the discrete-time loop.
    def test_terminal_commands_drive_the_game(self):
        self.assertEqual(self.game.command("add F1 T1 A 5 1"), "added")
        self.assertEqual(self.game.command("tick 2"), "2")
        self.assertEqual(self.game.command("wind W"), "wind-W")
        self.assertEqual(self.game.command("release F1 AW1"), "window-closed")
        self.assertEqual(self.game.command("wind N"), "wind-N")
        self.assertEqual(self.game.command("release F1 AW1"), "released")
        self.assertEqual(self.game.command("order"), "F1")
        self.assertEqual(self.game.command("cancel F1"), "cancelled")
        self.assertEqual(self.game.command("settle"), json.dumps("safe"))
        self.assertEqual(self.game.command(""), "empty")
        self.assertEqual(self.game.command("bogus"), "unknown")


if __name__ == "__main__":
    unittest.main()
