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
    def __init__(self, altitude, speed):
        self.altitude = altitude  # in km
        self.speed = speed  # in km/s

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
SAT_SAT_RANGE_SCALE = 5 # Changing scale to limit connection 
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
    """ x1 = random.uniform(0, planet_size_area)
    y1 = random.uniform(0, planet_size_area)
    x2 = clamp(planet_size_area - x1, 0, planet_size_area)
    y2 = clamp(planet_size_area - y1, 0, planet_size_area)
    return [x1, y1], [x2, y2] """

    h1_pos = [100, 150]
    h2_post = [300, 250]
    return h1_pos, h2_post

def can_see_sat_sat(sat_a, sat_b):
    # Get orbital layers based on altitude
    def get_layer(alt):
        if alt == 5:
            return 1  # Low orbit
        elif alt == 10:
            return 2  # Medium orbit
        else:  # alt == 15
            return 3  # High orbit
    
    layer_a = get_layer(sat_a.orbit.altitude)
    layer_b = get_layer(sat_b.orbit.altitude)
    
    # Calculate layer difference
    layer_diff = abs(layer_a - layer_b)
    
    # ENFORCE YOUR ACTUAL MESH TOPOLOGY:
    
    # Case 1: Same layer connections
    if layer_diff == 0:
        if layer_a == 2:  # Medium orbit (Sat2, Sat3)
            # Allow Sat2-Sat3 connection (they are connected in your network)
            names = {sat_a.name, sat_b.name}
            if "Sat2" in names and "Sat3" in names:
                max_range = 200
                return distance(sat_a.position, sat_b.position) <= max_range
            else:
                return False
                
        elif layer_a == 1:  # Low orbit (Sat4, Sat5, Sat6)
            # Allow mesh connections between low orbit satellites
            names = {sat_a.name, sat_b.name}
            # Allow Sat4-Sat5 and Sat5-Sat6 (your actual connections)
            if ("Sat4" in names and "Sat5" in names) or ("Sat5" in names and "Sat6" in names):
                max_range = 180
                return distance(sat_a.position, sat_b.position) <= max_range
            # Do NOT allow Sat4-Sat6 direct connection
            elif "Sat4" in names and "Sat6" in names:
                return False
            else:
                return False
                
        elif layer_a == 3:  # High orbit (only Sat1)
            return False  # No same-layer connections in high orbit
    
    # Case 2: Adjacent layers (3-2 or 2-1)
    elif layer_diff == 1:
        # High-to-Medium (3-2): Allow Sat1-Sat2 and Sat1-Sat3
        if (layer_a == 3 and layer_b == 2) or (layer_a == 2 and layer_b == 3):
            names = {sat_a.name, sat_b.name}
            if "Sat1" in names and ("Sat2" in names or "Sat3" in names):
                max_range = 250
                return distance(sat_a.position, sat_b.position) <= max_range
            else:
                return False
        
        # Medium-to-Low (2-1): Allow ALL possible connections
        # Your network has R2 connected to R4,R5 and R3 connected to R5,R6
        elif (layer_a == 2 and layer_b == 1) or (layer_a == 1 and layer_b == 2):
            names = {sat_a.name, sat_b.name}
            
            # Allow all medium-low combinations that exist in your network
            # Sat2 with Sat4 or Sat5
            if "Sat2" in names and ("Sat4" in names or "Sat5" in names):
                max_range = 220
                return distance(sat_a.position, sat_b.position) <= max_range
            
            # Sat3 with Sat5 or Sat6
            elif "Sat3" in names and ("Sat5" in names or "Sat6" in names):
                max_range = 220
                return distance(sat_a.position, sat_b.position) <= max_range
            
            # Do NOT allow Sat2-Sat6 or Sat3-Sat4 (these don't exist in your network)
            else:
                return False
    
    # Case 3: Non-adjacent layers (3-1)
    else:  # layer_diff == 2
        return False  # Never allow high orbit to connect to low orbit
    
    return False

def can_see_sat_ground(sat, ground_pos, host_positions, ground_name):
    # Only specific low-orbit satellites can see specific ground stations
    if sat.orbit.altitude != 5:  # Only altitude 5 satellites
        return False
    
    # Sat4 can only see Host1
    if sat.name == "Sat4" and ground_name == "Host1":
        max_range = 150
        return distance(sat.position, ground_pos) <= max_range
    
    # Sat6 can only see Host2
    elif sat.name == "Sat6" and ground_name == "Host2":
        max_range = 150
        return distance(sat.position, ground_pos) <= max_range
    
    # Sat5 can never see any ground station
    # Any other combination is invalid
    return False

def place_ground_stations(planet_size_root):
    # Place ground stations at strategic locations
    h1_pos = [100, 150]  # Adjusted these values
    h2_pos = [300, 250]  # based on where satellites orbit
    return h1_pos, h2_pos

def orbit_side_for_altitude(planet_size_root, altitude):
    return (planet_size_root + altitude) * (planet_size_root + altitude)

def main():

    is_running = True  # Control variable for starting/stopping the simulation

    # Example usage
    orbit1 = Orbit(altitude=ORBIT_ALTITUDE[0], speed=ORBIT_SPEED[0])
    orbit2 = Orbit(altitude=ORBIT_ALTITUDE[1], speed=ORBIT_SPEED[1])
    orbit3 = Orbit(altitude=ORBIT_ALTITUDE[2], speed=ORBIT_SPEED[2])

    host1 = GroundStation(name="Host1", links=[Link(state='up', bandwidth=100, latency=10)])
    host2 = GroundStation(name="Host2", links=[Link(state='up', bandwidth=100, latency=10)])

    switch1 = Satellite(name="Sat1", links=[Link(state='up', bandwidth=100, latency=10)], orbit=orbit3)
    switch2 = Satellite(name="Sat2", links=[Link(state='up', bandwidth=100, latency=10)], orbit=orbit2)   
    switch3 = Satellite(name="Sat3", links=[Link(state='up', bandwidth=100, latency=10)], orbit=orbit2)
    switch4 = Satellite(name="Sat4", links=[Link(state='up', bandwidth=100, latency=10)], orbit=orbit1)
    switch5 = Satellite(name="Sat5", links=[Link(state='up', bandwidth=100, latency=10)], orbit=orbit1)
    switch6 = Satellite(name="Sat6", links=[Link(state='up', bandwidth=100, latency=10)], orbit=orbit1)

    satellites = [switch1, switch2, switch3, switch4, switch5, switch6]
    ground_stations = [host1, host2]

    planet = Planet(size=3, orbits=[orbit1, orbit2, orbit3])
    ntn = NTN(satellites=satellites, ground_stations=ground_stations, planet=planet)

    #print(planet.l1_area, planet.l2_area, planet.l3_area)

    # host1_pos, host2_pos = place_ground_stations(planet.size_area)
    host1_pos, host2_pos = place_ground_stations(planet.size_root) # Changed to pass root and not the area 
    host_positions = {host1.name: host1_pos, host2.name: host2_pos}

    sim_number = get_next_sim_number(CSV_PATH)
    total_ticks = TICKS_PER_MINUTE * SIM_DURATION_MINUTES
    dt = 60 / TICKS_PER_MINUTE
    center = [ntn.grid_size / 2, ntn.grid_size / 2]

    orbit_groups = {}
    for sat in satellites:
        orbit_groups.setdefault(sat.orbit, []).append(sat)
    orbit_phase = {}
    for orbit, sats in orbit_groups.items():
        side = orbit_side_for_altitude(planet.size_root, orbit.altitude)
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
            for sat in satellites:
                orbit_side = orbit_side_for_altitude(planet.size_root, sat.orbit.altitude)
                half_side = orbit_side / 2
                phase = orbit_phase.get(sat.name, 0)
                sat.position = sat.orbit.update_position(time_s, center, half_side, phase)
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
                    if can_see_sat_ground(sat, host_pos, host_positions, host_name):
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
