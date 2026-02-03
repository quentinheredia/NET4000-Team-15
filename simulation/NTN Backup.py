import csv
import math
import os
import random

class NTN:
    def __init__(self, satellites, ground_stations, planet):    
        self.satellites = satellites  # list of Satellite objects
        self.ground_stations = ground_stations  # list of GroundStation objects
        self.planet = planet  # Planet object
        self.grid_size = planet.l3_area
    pass

class Planet:
    # radius (it will be a square for now)
    # atmosphere
    # rotation period

    def __init__(self,size,orbits):
        # Store areas (size^2) since the model uses a square planet/layers.
        self.size_area = size * size  # in km^2
        self.l1_area = (size + orbits[0].altitude) * (size + orbits[0].altitude) 
        self.l2_area = (size + orbits[1].altitude) * (size + orbits[1].altitude)
        self.l3_area = (size + orbits[2].altitude) * (size + orbits[2].altitude)
        # Backward-compatible aliases for existing code.
        self.size = self.size_area
        self.l1_atmosphere = self.l1_area
        self.l2_atmosphere = self.l2_area
        self.l3_atmosphere = self.l3_area

        pass
    pass

class Orbit:
    def __init__(self, altitude, speed):
        self.altitude = altitude  # in km
        self.speed = speed  # in km/s

    def update_position(self, time_s, center, radius):
        # Simple circular motion around center.
        if radius <= 0:
            return [center[0], center[1]]
        angular_speed = self.speed / radius  # rad/s
        angle = angular_speed * time_s
        x = center[0] + radius * math.cos(angle)
        y = center[1] + radius * math.sin(angle)
        return [x, y]

class Satellite:
    # switches
    def __init__(self, name, links, orbit):
        self.name = name
        self.links = links  # list of Link objects
        self.orbit = orbit  # Orbit object
        self.position = [0, 0]  # to be updated based on orbit

class GroundStation: 
    # host nodes
    def __init__(self, name, links):
        self.name = name
        self.links = links  # list of Link objects:

class Link:
    def __init__(self, state, bandwidth, latency):
        self.state = state  # 'up' or 'down'
        self.bandwidth = bandwidth  # in Mbps
        self.latency = latency  # in ms

# GLOBAL CONSTANTS
ORBIT_SPEED = [100,50,25] # Speed in km/s ????????????
ORBIT_ALTITUDE = [5,10,15] # Altitude in km ????????????
TICKS_PER_MINUTE = 6
SIM_DURATION_MINUTES = 1
SAT_SAT_RANGE_SCALE = 10
SAT_GROUND_RANGE_SCALE = 10
CSV_PATH = "simulation results.csv"

def clamp(value, low, high):
    return max(low, min(value, high))

def distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])

def get_next_sim_number(csv_path):
    if not os.path.exists(csv_path):
        return 1
    max_sim = 0
    with open(csv_path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                max_sim = max(max_sim, int(row.get("sim_number", 0)))
            except (TypeError, ValueError):
                continue
    return max_sim + 1

def place_ground_stations(planet_size_area):
    x1 = random.uniform(0, planet_size_area)
    y1 = random.uniform(0, planet_size_area)
    x2 = clamp(planet_size_area - x1, 0, planet_size_area)
    y2 = clamp(planet_size_area - y1, 0, planet_size_area)
    return [x1, y1], [x2, y2]

def can_see_sat_sat(sat_a, sat_b):
    max_range = (sat_a.orbit.altitude + sat_b.orbit.altitude) * SAT_SAT_RANGE_SCALE
    return distance(sat_a.position, sat_b.position) <= max_range

def can_see_sat_ground(sat, ground_pos):
    max_range = sat.orbit.altitude * SAT_GROUND_RANGE_SCALE
    return distance(sat.position, ground_pos) <= max_range

def main():

    is_running = True  # Control variable for starting/stopping the simulation

    # Example usage
    orbit1 = Orbit(altitude=ORBIT_ALTITUDE[0], speed=ORBIT_SPEED[0])
    orbit2 = Orbit(altitude=ORBIT_ALTITUDE[1], speed=ORBIT_SPEED[1])
    orbit3 = Orbit(altitude=ORBIT_ALTITUDE[2], speed=ORBIT_SPEED[2])

    host1 = GroundStation(name="Host1", links=[Link(state='up', bandwidth=100, latency=10)])
    host2 = GroundStation(name="Host2", links=[Link(state='up', bandwidth=100, latency=10)])

    switch1 = Satellite(name="Sat1", links=[Link(state='up', bandwidth=100, latency=10)], orbit=orbit1)
    switch2 = Satellite(name="Sat2", links=[Link(state='up', bandwidth=100, latency=10)], orbit=orbit1)   
    switch3 = Satellite(name="Sat3", links=[Link(state='up', bandwidth=100, latency=10)], orbit=orbit1)
    switch4 = Satellite(name="Sat4", links=[Link(state='up', bandwidth=100, latency=10)], orbit=orbit2)
    switch5 = Satellite(name="Sat5", links=[Link(state='up', bandwidth=100, latency=10)], orbit=orbit2)
    switch6 = Satellite(name="Sat6", links=[Link(state='up', bandwidth=100, latency=10)], orbit=orbit3)

    satellites = [switch1, switch2, switch3, switch4, switch5, switch6]
    ground_stations = [host1, host2]

    planet = Planet(size=5, orbits=[orbit1, orbit2, orbit3])
    ntn = NTN(satellites=satellites, ground_stations=ground_stations, planet=planet)

    host1_pos, host2_pos = place_ground_stations(planet.size_area)
    host_positions = {host1.name: host1_pos, host2.name: host2_pos}

    sim_number = get_next_sim_number(CSV_PATH)
    total_ticks = TICKS_PER_MINUTE * SIM_DURATION_MINUTES
    dt = 60 / TICKS_PER_MINUTE
    center = [ntn.grid_size / 2, ntn.grid_size / 2]

    write_header = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        fieldnames = [
            "sim_number",
            "time_s",
            "tick",
            "sat_name",
            "orbit_altitude",
            "orbit_speed",
            "x",
            "y",
            "can_see",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()

        for tick in range(total_ticks):
            if not is_running:
                break
            time_s = tick * dt
            for sat in satellites:
                radius = (planet.size_area / 2) + sat.orbit.altitude
                sat.position = sat.orbit.update_position(time_s, center, radius)
                sat.position[0] = clamp(sat.position[0], 0, ntn.grid_size)
                sat.position[1] = clamp(sat.position[1], 0, ntn.grid_size)

            for sat in satellites:
                visible = []
                for other in satellites:
                    if other is sat:
                        continue
                    if can_see_sat_sat(sat, other):
                        visible.append(other.name)
                for host_name, host_pos in host_positions.items():
                    if can_see_sat_ground(sat, host_pos):
                        visible.append(host_name)

                print(
                    f"{{{sat.name}, orbit_altitude:{sat.orbit.altitude}, "
                    f"pos:({sat.position[0]:.2f},{sat.position[1]:.2f}), "
                    f"can_see:{','.join(visible)}, time_s:{time_s:.2f}}}"
                )

                writer.writerow({
                    "sim_number": sim_number,
                    "time_s": f"{time_s:.2f}",
                    "tick": tick,
                    "sat_name": sat.name,
                    "orbit_altitude": sat.orbit.altitude,
                    "orbit_speed": sat.orbit.speed,
                    "x": f"{sat.position[0]:.6f}",
                    "y": f"{sat.position[1]:.6f}",
                    "can_see": ",".join(visible),
                })


    
    





if __name__ == "__main__":
    main()



