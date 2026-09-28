import json
import random


class TowerControl:
    """Discrete-time tower control simulation with terminal commands."""

    WIND_OPEN = ("N", "S")

    def __init__(self, seed=20240917):
        self.seed = seed
        self.rng = random.Random(seed)
        self.time = 0
        self.wind = "N"
        self.current_sector = "A"
        self.flights = {}
        self.runways = {"R1": None, "R2": None}
        self.sectors = {"A": [], "B": [], "C": []}
        self.airways = set()
        self.broadcasts = []
        self.diverted = set()
        self.queue = []
        self._order = 0

    # --- tracks ---------------------------------------------------------
    def add_flight(self, fid, track, sector, fuel=5, priority=1):
        if fid in self.flights:
            return "duplicate"
        if sector not in self.sectors:
            return "no-sector"
        self.flights[fid] = {
            "id": fid,
            "track": track,
            "sector": sector,
            "fuel": fuel,
            "priority": priority,
            "status": "enroute",
            "route": None,
            "enter": self.time,
            "leave": None,
            "order": self._order,
        }
        self._order += 1
        self.sectors[sector].append(fid)
        return "added"

    def _leave_sector(self, flight):
        if flight["leave"] is None:
            flight["leave"] = self.time

    def _interval(self, flight):
        leave = flight["leave"] if flight["leave"] is not None else self.time
        return (flight["enter"], leave)

    @staticmethod
    def _overlap(a_enter, a_leave, b_enter, b_leave):
        # Boundary moments count as a conflict: intervals are inclusive.
        return a_enter <= b_leave and b_enter <= a_leave

    def conflicts(self, sector=None):
        names = [sector] if sector else sorted(self.sectors)
        found = []
        for name in names:
            ids = self.sectors[name]
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    first = self.flights[ids[i]]
                    second = self.flights[ids[j]]
                    if first["track"] != second["track"]:
                        continue
                    a_enter, a_leave = self._interval(first)
                    b_enter, b_leave = self._interval(second)
                    if self._overlap(a_enter, a_leave, b_enter, b_leave):
                        found.append((first["id"], second["id"]))
        return sorted(found)

    # --- time -----------------------------------------------------------
    def tick(self, steps=1):
        for _ in range(steps):
            self.time += 1
            for flight in self.flights.values():
                if flight["status"] == "enroute" and flight["fuel"] > 0:
                    flight["fuel"] -= 1
                if flight["status"] == "landing":
                    flight["status"] = "landed"
                    for name, occupant in self.runways.items():
                        if occupant == flight["id"]:
                            self.runways[name] = None
        return self.time

    # --- runway ---------------------------------------------------------
    def land(self, fid, runway):
        flight = self.flights.get(fid)
        if flight is None:
            return "no-flight"
        if flight["status"] == "landed":
            return "already-landed"
        if flight["status"] != "enroute":
            return "unavailable"
        if runway not in self.runways:
            return "no-runway"
        if self.runways[runway] is not None:
            return "occupied"
        self.runways[runway] = fid
        flight["status"] = "landing"
        self._leave_sector(flight)
        return "landing"

    # --- diversion ------------------------------------------------------
    def divert(self, fid, alternate):
        if fid in self.diverted:
            return "duplicate"
        flight = self.flights.get(fid)
        if flight is None:
            return "no-flight"
        if alternate not in self.sectors:
            return "no-sector"
        self.diverted.add(fid)
        alt_id = "%s-ALT%d" % (fid, self.rng.randint(100, 999))
        self.flights[alt_id] = {
            "id": alt_id,
            "track": flight["track"],
            "sector": alternate,
            "fuel": flight["fuel"],
            "priority": flight["priority"],
            "status": "enroute",
            "route": None,
            "enter": self.time,
            "leave": None,
            "order": self._order,
        }
        self._order += 1
        self.sectors[alternate].append(alt_id)
        flight["status"] = "diverted"
        self._leave_sector(flight)
        self.broadcasts.append("divert %s -> %s" % (fid, alternate))
        return alt_id

    # --- wind and departure window --------------------------------------
    def set_wind(self, direction):
        self.wind = direction
        return "wind-" + direction

    def departure_window(self):
        if self.wind in self.WIND_OPEN:
            return (self.time, self.time + 2)
        return None

    def can_depart(self):
        return self.departure_window() is not None

    # --- fuel -----------------------------------------------------------
    def delay(self, fid, ticks=1):
        flight = self.flights.get(fid)
        if flight is None:
            return "no-flight"
        if flight["fuel"] <= 0 or ticks > flight["fuel"]:
            return "no-fuel"
        flight["fuel"] -= ticks
        return "delayed"

    # --- release and cancel ---------------------------------------------
    def release(self, fid, route):
        flight = self.flights.get(fid)
        if flight is None:
            return "no-flight"
        if not self.can_depart():
            return "window-closed"
        if route in self.airways:
            return "route-busy"
        self.airways.add(route)
        flight["route"] = route
        flight["status"] = "released"
        if fid not in self.queue:
            self.queue.append(fid)
        return "released"

    def cancel(self, fid):
        flight = self.flights.get(fid)
        if flight is None:
            return "no-flight"
        if flight["route"] is not None:
            self.airways.discard(flight["route"])
            flight["route"] = None
        if fid in self.queue:
            self.queue.remove(fid)
        if flight["status"] == "released":
            flight["status"] = "enroute"
        return "cancelled"

    # --- priority ---------------------------------------------------------
    def departure_order(self):
        return sorted(
            self.queue,
            key=lambda fid: (-self.flights[fid]["priority"], self.flights[fid]["order"]),
        )

    # --- broadcast --------------------------------------------------------
    def broadcast(self, message):
        self.broadcasts.append(message)
        return "broadcast"

    # --- settlement -------------------------------------------------------
    def settle(self):
        found = self.conflicts()
        return found if found else "safe"

    # --- JSON archive -----------------------------------------------------
    def save(self, path):
        data = {
            "seed": self.seed,
            "time": self.time,
            "wind": self.wind,
            "current_sector": self.current_sector,
            "flights": self.flights,
            "runways": self.runways,
            "sectors": self.sectors,
            "airways": sorted(self.airways),
            "broadcasts": self.broadcasts,
            "diverted": sorted(self.diverted),
            "queue": self.queue,
            "order": self._order,
            "rng_state": json.loads(json.dumps(self.rng.getstate())),
        }
        with open(path, "w") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        return "saved"

    def load(self, path):
        with open(path) as handle:
            data = json.load(handle)
        self.seed = data["seed"]
        self.rng = random.Random(self.seed)
        state = data["rng_state"]
        self.rng.setstate((state[0], tuple(state[1]), state[2]))
        self.time = data["time"]
        self.wind = data["wind"]
        self.current_sector = data["current_sector"]
        self.flights = data["flights"]
        self.runways = data["runways"]
        self.sectors = data["sectors"]
        self.airways = set(data["airways"])
        self.broadcasts = list(data["broadcasts"])
        self.diverted = set(data["diverted"])
        self.queue = list(data["queue"])
        self._order = data["order"]
        return "loaded"

    # --- terminal commands ------------------------------------------------
    def command(self, line):
        parts = line.split()
        if not parts:
            return "empty"
        cmd, args = parts[0], parts[1:]
        if cmd == "add":
            fuel = int(args[3]) if len(args) > 3 else 5
            priority = int(args[4]) if len(args) > 4 else 1
            return self.add_flight(args[0], args[1], args[2], fuel, priority)
        if cmd == "tick":
            return str(self.tick(int(args[0]) if args else 1))
        if cmd == "wind":
            return self.set_wind(args[0])
        if cmd == "land":
            return self.land(args[0], args[1])
        if cmd == "delay":
            return self.delay(args[0], int(args[1]) if len(args) > 1 else 1)
        if cmd == "release":
            return self.release(args[0], args[1])
        if cmd == "cancel":
            return self.cancel(args[0])
        if cmd == "divert":
            return self.divert(args[0], args[1])
        if cmd == "broadcast":
            return self.broadcast(" ".join(args))
        if cmd == "settle":
            return json.dumps(self.settle())
        if cmd == "order":
            return " ".join(self.departure_order())
        if cmd == "save":
            return self.save(args[0])
        if cmd == "load":
            return self.load(args[0])
        return "unknown"
