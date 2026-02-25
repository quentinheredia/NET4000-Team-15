#!/usr/bin/env python3

import csv 
import subprocess
import time 
import math 
from collections import defaultdict
import sys
import signal

sys.stdout.reconfigure(line_buffering=True)
CSV_FILE = "simulation results.csv"
Sim_num = 1 
TICK_INTERVAL = 10 # based on results, we set each tick to be 10 real world seconds 


def signal_handler(sig, frame):
    print("\n\nInterrupt received! Cleaning up...")
    cleanup_topology()
    sys.exit(0)

# Register the signal handler
signal.signal(signal.SIGINT, signal_handler)

# Linking satellite names to interfaces

LINK_MAP = {
    # R1 (Sat1) connections
    "Sat1-Sat2": {"ns": "r1", "iface": "v-r1-r2"},
    "Sat1-Sat3": {"ns": "r1", "iface": "v-r1-r3"},
    "Sat2-Sat1": {"ns": "r2", "iface": "v-r2-r1"},
    "Sat3-Sat1": {"ns": "r3", "iface": "v-r3-r1"},
    
    # R2 (Sat2) connections
    "Sat2-Sat4": {"ns": "r2", "iface": "v-r2-r4"},
    "Sat2-Sat5": {"ns": "r2", "iface": "v-r2-r5"},
    "Sat2-Sat3": {"ns": "r2", "iface": "v-r2-r3"},  # You have this link!
    "Sat4-Sat2": {"ns": "r4", "iface": "v-r4-r2"},
    "Sat5-Sat2": {"ns": "r5", "iface": "v-r5-r2"},
    "Sat3-Sat2": {"ns": "r3", "iface": "v-r3-r2"},  # You have this link!
    
    # R3 (Sat3) connections
    "Sat3-Sat5": {"ns": "r3", "iface": "v-r3-r5"},
    "Sat3-Sat6": {"ns": "r3", "iface": "v-r3-r6"},
    "Sat5-Sat3": {"ns": "r5", "iface": "v-r5-r3"},
    "Sat6-Sat3": {"ns": "r6", "iface": "v-r6-r3"},
    
    # R4 (Sat4) connections
    "Sat4-Sat2": {"ns": "r4", "iface": "v-r4-r2"},
    "Sat4-Sat5": {"ns": "r4", "iface": "v-r4-r5"},  # You have this link!
    "Sat4-Host1": {"ns": "r4", "iface": "v-r4-h1"},
    "Sat5-Sat4": {"ns": "r5", "iface": "v-r5-r4"},  # You have this link!
    
    # R5 (Sat5) connections
    "Sat5-Sat2": {"ns": "r5", "iface": "v-r5-r2"},
    "Sat5-Sat3": {"ns": "r5", "iface": "v-r5-r3"},
    "Sat5-Sat4": {"ns": "r5", "iface": "v-r5-r4"},
    "Sat5-Sat6": {"ns": "r5", "iface": "v-r5-r6"},  # You have this link!
    
    # R6 (Sat6) connections
    "Sat6-Sat3": {"ns": "r6", "iface": "v-r6-r3"},
    "Sat6-Sat5": {"ns": "r6", "iface": "v-r6-r5"},  # You have this link!
    "Sat6-Host2": {"ns": "r6", "iface": "v-r6-h2"},
    
    # Host connections
    "Host1-Sat4": {"ns": "h1", "iface": "v-h1-r4"},
    "Host2-Sat6": {"ns": "h2", "iface": "v-h2-r6"},
}

# Cleanup function to ensure topology is normal 
def cleanup_topology():
    
    print("\n Resetting Topology to default state")
    for link_key, link_info in LINK_MAP.items():
        ns = link_info["ns"]
        iface = link_info["iface"]
        
        # Remove all network emulation (delay, loss, jitter)
        run(f"ip netns exec {ns} tc qdisc del dev {iface} root 2>/dev/null || true", quiet=True)
        print(f"  Reset {link_key} ({ns}:{iface})")
    
    print("\n All interfaces reset to normal state")

# Simulating delay
def compute_delay_ms(alt_a, alt_b, x1, y1, x2, y2):
    # Estimate delay based on altitude and distance 
    distance = math.sqrt((x2-x1)**2 + (y2-y1)**2)
    average_alt = (alt_a + alt_b) / 2
    base_delay = average_alt * 8 # this and distance delay currently are arbritrary values until we know what we are making them
    distance_delay = distance * 0.05
    return round(base_delay + distance_delay, 1)

def compute_jitter(delay_ms):
    return round(delay_ms * 0.05, 1) # 5% jitter 


# Applying these to actual namespace using TC NETEM

DRY_RUN = True # Flip to false for actual implementation, using this to test 

def run(cmd, quiet=False):
    # Logic to prevent it from running
    if DRY_RUN and not quiet: 
        print(f" [DRY RUN] {cmd}")
        return 
    elif DRY_RUN and quiet:
        return
    
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0 and "File exists" not in result.stderr and "No such file" not in result.stderr:
        print(f"[warn] {cmd} \n {result.stderr.strip()}")


def apply_link_up(link_key, delay_ms, jitter_ms):
    if link_key not in LINK_MAP:
        return 
    ns = LINK_MAP[link_key]["ns"]
    iface = LINK_MAP[link_key]["iface"]

    # Remove any existing qdisc first to avoid conflicts
    run(f"ip netns exec {ns} tc qdisc del dev {iface} root 2>/dev/null || true", quiet=True)

    # Add new qdisc with delay
    run(f"ip netns exec {ns} tc qdisc add dev {iface} root netem delay {delay_ms}ms {jitter_ms}ms distribution normal loss 0%")
    print(f"  {link_key}: delay={delay_ms}ms +- {jitter_ms}ms --> UP")

def apply_link_down(link_key):
    if link_key not in LINK_MAP:
        return 
    ns = LINK_MAP[link_key]["ns"]
    iface = LINK_MAP[link_key]["iface"]

    # Remove any existing qdisc first
    run(f"ip netns exec {ns} tc qdisc del dev {iface} root 2>/dev/null || true", quiet=True)
    
    # Add loss to simulate down
    run(f"ip netns exec {ns} tc qdisc add dev {iface} root netem loss 100%")
    print(f"  {link_key}: --> DOWN, NO LINE OF SIGHT")

# CSV PARSING 

def load_simulation(csv_file, sim_number):
    ticks = defaultdict(dict)
    with open(csv_file, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if int(row['sim_number']) != sim_number:
                continue 
            tick = int(row['tick'])
            sat = row['sat_name']
            can_see_raw = row['can_see'].strip()
            can_see = [s.strip() for s in can_see_raw.split(',') if s.strip()] if can_see_raw else []
            ticks[tick][sat] = {
                'alt': float(row['orbit_altitude']),
                'x': float(row['x']),
                'y': float(row['y']),
                'can_see': can_see,
            }
    return dict(sorted(ticks.items())) # ensures that all elements are done in correct time order 


# FINALLY MAIN LOOP

def apply_tick(tick_num, sat_states):
    print(f"Tick {tick_num} --> APPLYING TO NAMESPACE")

    # building set of active links per tick 
    active_links = set()
    for sat_a, state_a in sat_states.items():
        for sat_b in state_a['can_see']:
            link_key = f"{sat_a}-{sat_b}"
            active_links.add(link_key)

    # Applying UP/DOWN to every link 
    for link_key in LINK_MAP:
        sat_a, sat_b = link_key.split('-', 1)
        if link_key in active_links:
            # endpoints do exist within the tick output
            if sat_a in sat_states and sat_b in sat_states:
                state_a = sat_states[sat_a]
                state_b = sat_states[sat_b]
                delay = compute_delay_ms(
                    state_a['alt'], state_b['alt'],
                    state_a['x'], state_b['x'],
                    state_a['y'], state_b['y'],
                )
                jitter = compute_jitter(delay)
                apply_link_up(link_key, delay, jitter)
            else:
                apply_link_down(link_key)
        else:
            apply_link_down(link_key)

def main():
    print(f"Loading simulation {Sim_num} from {CSV_FILE}...")
    sim_ticks = load_simulation(CSV_FILE, Sim_num)
    print(f"found {len(sim_ticks)} ticks: {list(sim_ticks.keys())}")

    
    try:
        # Modes to test topology per tick 
        mode = input("Choose automatic (a) or manual (m). [a/m]").strip().lower()

        if mode == "m":
            # Manual - go through each tick individually
            print("Manual mode selected, press enter to move to the next tick, and press q to quit.")

            for tick_num, sat_states in sim_ticks.items():
                print(f"\nReady for tick {tick_num}.")
                
                # Show what links will be active
                print("Links that will be UP:")
                for sat_a, state_a in sat_states.items():
                    for sat_b in state_a['can_see']:
                        print(f"  {sat_a} -> {sat_b}")
                
                user_input = input("Press Enter to apply this tick, or 'q' to quit: ").strip().lower()

                if user_input == 'q':
                    print("Exiting simulation.")
                    break

                apply_tick(tick_num, sat_states)
                print(f"tick {tick_num} applied. Waiting for next input, now you can test network")
                
                if tick_num != list(sim_ticks.keys())[-1]:
                    input("Press Enter for the next tick")
        else:
            try:
                custom_delay = input(f"Enter delay between ticks in seconds (default {TICK_INTERVAL}): ")
                if custom_delay.strip():
                    delay = float(custom_delay)
                else:
                    delay = TICK_INTERVAL
            except ValueError:
                delay = TICK_INTERVAL
                print(f"Invalid input, using default: {delay}s")
            
            for tick_num, sat_states in sim_ticks.items():
                print(f"\nApplying Tick {tick_num}...")
                apply_tick(tick_num, sat_states)
                print(f"Tick {tick_num} applied. Waiting {delay} seconds...")
                time.sleep(delay)

    finally:
        # NEW: Always cleanup when done
        cleanup_topology()
        print("\nSimulation Cleaned up")

    print("\n Simulation Complete")

if __name__ == "__main__":
    main()