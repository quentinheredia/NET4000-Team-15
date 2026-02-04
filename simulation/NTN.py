import csv
import math
import os
import random

class NTN:
    def __init__(self, satellites, ground_stations, planet):    
        self.satellites = satellites  # list of Satellite objects
        self.ground_stations = ground_stations  # list of GroundStation objects
        self.planet = planet  # Planet object
        self.grid_size = (planet.l3_area) * 2
    pass

class Planet:
    # radius (it will be a square for now)
    # atmosphere
    # rotation period

    def __init__(self,size,orbits):
        # Store areas (size^2) since the model uses a square planet/layers.
        self.size_area = size * size  # in km^2
        self.size_root = size  # side length (km)
        self.l1_area = (size + orbits[0].altitude) * (size + orbits[0].altitude) 
        # 5 + 5 = 10  10 * 10 = 100
        self.l2_area = (size + orbits[1].altitude) * (size + orbits[1].altitude)
        # 5 + 10 = 15  15 * 15 = 225
        self.l3_area = (size + orbits[2].altitude) * (size + orbits[2].altitude)
        # 5 + 15 = 20  20 * 20 = 400
        # Backward-compatible aliases for existing code.
        self.size = self.size_area
        self.l1_atmosphere = self.l1_area
        self.l2_atmosphere = self.l2_area
        self.l3_atmosphere = self.l3_area

        pass
    pass

class Orbit:
    def __init__(self, level):

        self.level = level

        if (self.level == 'L0'):
            self.speed = 0
            self.altitude = 0
        elif (self.level == 'L1'):
            self.speed = 100
            self.altitude = 5        
        elif (self.level == 'L2'):
            self.speed = 50
            self.altitude = 10
        elif (self.level == 'L3'):
            self.speed = 25  # Assuming same speed as L2 for L3
            self.altitude = 15
        else:
           self.speed = 0  # Default to L0 speed if unknown level
           self.altitude = 0
       

        self.level = level  # L0, L1, L2, L3

    def update_position(self, time_s, center, half_side, phase_offset):
        # Square orbit, clockwise, constrained to the orbit path.
        if half_side <= 0:
            return [center[0], center[1]]
        side = half_side * 2
        perimeter = side * 4
        distance_travelled = (phase_offset + self.speed * time_s) % perimeter

        # Start at top-right, move clockwise: down, left, up, right.
        x = center[0] + half_side
        y = center[1] + half_side

        if distance_travelled <= side:
            y -= distance_travelled
        elif distance_travelled <= side * 2:
            y -= side
            x -= (distance_travelled - side)
        elif distance_travelled <= side * 3:
            x -= side
            y += (distance_travelled - side * 2)
        else:
            y += side
            x += (distance_travelled - side * 3)

        return [x, y]

class Satellite:
    # switches
    def __init__(self, name, links, orbit):
        self.name = name
        self.links = []
        for i in range(links):
            self.links.append(Link(name =f"L0/0/{i}",status='up', bandwidth=100, latency=10, type=orbit.level))
            i+=1
        self.orbit = orbit  # Orbit object
        self.position = [0, 0]  # to be updated based on orbit
        self.maxConnect = 5  # default max connections, can be overridden

    def __init__(self, name, links, orbit, maxConnect=5):
        self.name = name
        self.links = []
        for i in range(links):
            self.links.append(Link(name =f"L0/0/{i}",status='up', bandwidth=100, latency=10, type=orbit.level))
            i+=1

        self.orbit = orbit  # Orbit object
        self.position = [0, 0]  # to be updated based on orbit
        self.maxConnect = maxConnect  # maximum number of connections

    def __connect__(self, Sattelite_Self, Satellite_Other):
        for Link in self.links:
            if can_see_sat_sat(Sattelite_Self, Satellite_Other):
                print("Connect from {Sattelite_Self.name} to {Satellite_Other.name} succeeded.")
                Link.__setConnectedState__(True)
            else:
                print("Connect from {Sattelite_Self.name} to {Satellite_Other.name} failed: out of range.")
                Link.__setConnectedState__(False)

    def __cansee__(self, satellites, host_positions=None):
        visible = []
        for other in satellites:
            if other is self:
                continue
            if can_see_sat_sat(self, other):
                visible.append(other.name)
        if host_positions:
            for host_name, host_pos in host_positions.items():
                if can_see_sat_ground(self, host_pos):
                    visible.append(host_name)
        return visible

    
class GroundStation: 
    # host nodes
    def __init__(self, name, links):
        self.name = name
        self.links = []
        for i in range(links):
            self.links.append(Link(name =f"L0/0/{i}",status='up', bandwidth=100, latency=10, type='L0'))
            i+=1

    def __connect__(self, Sattelite_Self, Satellite_Other):
        for Link in self.links:
            if can_see_sat_sat(Sattelite_Self, Satellite_Other):
                print("Connect from {Sattelite_Self.name} to {Satellite_Other.name} succeeded.")
                Link.__setConnectedState__(True)
            else:
                print("Connect from {Sattelite_Self.name} to {Satellite_Other.name} failed: out of range.")
                Link.__setConnectedState__(False)

class Link:
    def __init__(self, name, status, bandwidth, latency, type):
        self.name = name
        self.status = status  # 'up' or 'down'
        self.isConnected = False  # 'connected' or 'disconnected'
        self.bandwidth = bandwidth  # in Mbps
        self.latency = latency  # in ms
        self.type = type  # 'L0 = Ground, L1 = Low Earth Orbit, L2 = Medium Earth Orbit, L3 = Geostationary Orbit'

    def __getstatus__(self):
        return self.status 
    
    def __setstatus__(self, state):
        self.status = state

    def __setConnectedState__(self, isConnected):
        self.isConnected = isConnected

    def __getConnectedState__(self):
        return self.isConnected


# GLOBAL CONSTANTS
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

def orbit_side_for_altitude(planet_size_root, altitude):
    return (planet_size_root + altitude) * (planet_size_root + altitude)

def node_info(node, list):
    if (node == 'satellite'):
        for i in range(len(list)):
            satellite = list[i]
            print(f"Satellite Name: {satellite.name}, Orbit: {satellite.orbit.altitude},")
            for link in satellite.links:
                print(f"Link name: {link.name}, Link Type: {link.type} status: {link.status}, bandwidth: {link.bandwidth} Mbps, latency: {link.latency} ms")
            i+=1
    elif (node == 'groundstation'):
        for i in range(len(list)):
            ground_station = list[i]
            print(f"Ground Station Name: {ground_station.name}")
            for link in ground_station.links:
                print(f"Link name: {link.name}, Link Type: {link.type} status: {link.status}, bandwidth: {link.bandwidth} Mbps, latency: {link.latency} ms")
            i+=1
    return 1


def load_defaults():
    
    orbit0 = Orbit(level='L0')
    orbit1 = Orbit(level='L1')
    orbit2 = Orbit(level='L2')
    orbit3 = Orbit(level='L3')

    host1 = GroundStation(name="Host1", links=1)
    host2 = GroundStation(name="Host2", links=1)

    switch1 = Satellite(name="Sat1", links=5, orbit=orbit1)
    switch2 = Satellite(name="Sat2", links=5, orbit=orbit1)   
    switch3 = Satellite(name="Sat3", links=5, orbit=orbit1)
    switch4 = Satellite(name="Sat4", links=5, orbit=orbit2)
    switch5 = Satellite(name="Sat5", links=5, orbit=orbit2)
    switch6 = Satellite(name="Sat6", links=5, orbit=orbit3)

    satellites = [switch1, switch2, switch3, switch4, switch5, switch6]
    ground_stations = [host1, host2]

    planet = Planet(size=5, orbits=[orbit1, orbit2, orbit3])

    return NTN(satellites=satellites, ground_stations=ground_stations, planet=planet)


def main():

    SIMULATION = load_defaults() 

    node_info('satellite', SIMULATION.satellites)
    node_info('groundstation', SIMULATION.ground_stations)

    is_running = True  # Control variable for starting/stopping the simulation

    #print(planet.l1_area, planet.l2_area, planet.l3_area)

    host1_pos, host2_pos = place_ground_stations(SIMULATION.planet.size_area)
    host_positions = {SIMULATION.ground_stations[0].name: host1_pos, SIMULATION.ground_stations[1].name: host2_pos}

    sim_number = get_next_sim_number(CSV_PATH)
    total_ticks = TICKS_PER_MINUTE * SIM_DURATION_MINUTES
    dt = 60 / TICKS_PER_MINUTE
    center = [SIMULATION.grid_size / 2, SIMULATION.grid_size / 2]

    orbit_groups = {}
    for sat in SIMULATION.satellites:
        orbit_groups.setdefault(sat.orbit, []).append(sat)
    orbit_phase = {}
    for orbit, sats in orbit_groups.items():
        side = orbit_side_for_altitude(SIMULATION.planet.size_root, orbit.altitude)
        perimeter = side * 4
        step = perimeter / max(1, len(sats))
        for i, sat in enumerate(sats):
            orbit_phase[sat.name] = i * step

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
            for sat in SIMULATION.satellites:
                orbit_side = orbit_side_for_altitude(SIMULATION.planet.size_root, sat.orbit.altitude)
                half_side = orbit_side / 2
                phase = orbit_phase.get(sat.name, 0)
                sat.position = sat.orbit.update_position(time_s, center, half_side, phase)
                sat.position[0] = clamp(sat.position[0], 0, SIMULATION.grid_size)
                sat.position[1] = clamp(sat.position[1], 0, SIMULATION.grid_size)

            for sat in SIMULATION.satellites:
                visible = sat.__cansee__(SIMULATION.satellites, host_positions)
                #print(
                #    f"{{{sat.name}, orbit_altitude:{sat.orbit.altitude}, "
                #    f"pos:({sat.position[0]:.2f},{sat.position[1]:.2f}), "
                #    f"can_see:{','.join(visible)}, time_s:{time_s:.2f}}}"
                #)

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
