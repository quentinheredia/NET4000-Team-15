# NET4000-Team-15

**Team Members:**

- Ishani Singh
- Nicki Karimi
- Quentin Heredia
- Shawn Rae

---

## Project — Delay-Predictive Routing for Emulated Non-Terrestrial Networks

**Objective:** Build an emulated Non-Terrestrial Network (NTN) and evaluate delay-predictive routing, comparing it against standard OSPF routing.

### Goals

- Create a dynamic topology with realistic NTN scenarios
- Implement OSPF-based routing using FRR inside Linux network namespaces
- Simulate satellite movement and link-state changes over time
- Collect telemetry data from the live topology
- Train a regression model to predict near-future link/path delay
- Integrate predicted delay into routing decisions and analyze the results

---

## Architecture Overview

The project is split into four main components that work together in a pipeline:

```
[simulation/NTN.py]  →  [simulation results.csv]  →  [ntn_mlm.py]  →  [namespace-network/attempt-to-link.py]
   Satellite orbit            Tick-by-tick              Train & save      Apply predicted delays &
   simulation                position + visibility      delay model       link states to live namespaces
                             data
```

1. **`simulation/`** — Models a satellite constellation moving through orbits, computing visibility and position per tick, and writing results to CSV.
2. **`simulation results.csv`** — The data bridge between the simulation and the live network. Each row captures one satellite's state at one point in time.
3. **`ntn_mlm.py`** — Generates rich training data from the orbit simulation, trains a Gradient Boosted regression model to predict one-tick-ahead link delay, and exposes a `predict_next_tick()` function for integration into the routing pipeline.
4. **`namespace-network/`** — Sets up a real emulated network using Linux namespaces and FRR/OSPF, then replays the simulation data by applying delays and link-state changes to the live topology.

---

## Component Details

### `namespace-network/`

This folder contains the scripts that build and operate the emulated network on a real Linux host.

#### `new-net-namespace.sh` — Network Setup Script

This Bash script creates the entire network topology from scratch using Linux network namespaces and [FRR (Free Range Routing)](https://frrouting.org/).

**What it does:**

1. **Cleanup** — Tears down any leftover namespaces, veth pairs, and FRR processes from previous runs to ensure a clean slate.

2. **Namespace creation** — Creates 8 isolated network namespaces: six routers (`r1`–`r6`) representing satellites, and two hosts (`h1`, `h2`) representing ground stations. IP forwarding is enabled on all router namespaces.

3. **veth link wiring** — Connects namespaces using virtual Ethernet (`veth`) pairs. Each pair creates a point-to-point link between two namespaces. The topology mirrors a layered satellite mesh:

   ```
          R1          ← High orbit (Sat1)
       R2    R3       ← Medium orbit (Sat2, Sat3)
    R4    R5    R6    ← Low orbit (Sat4, Sat5, Sat6)
    H1              H2
   ```

   Full link list and IP scheme:

   | Link    | Interface pair    | Subnet      |
   | ------- | ----------------- | ----------- |
   | H1 ↔ R4 | v-h1-r4 / v-r4-h1 | 10.0.4.0/24 |
   | H2 ↔ R6 | v-h2-r6 / v-r6-h2 | 10.0.6.0/24 |
   | R4 ↔ R2 | v-r4-r2 / v-r2-r4 | 10.4.2.0/24 |
   | R5 ↔ R2 | v-r5-r2 / v-r2-r5 | 10.5.2.0/24 |
   | R5 ↔ R3 | v-r5-r3 / v-r3-r5 | 10.5.3.0/24 |
   | R6 ↔ R3 | v-r6-r3 / v-r3-r6 | 10.6.3.0/24 |
   | R2 ↔ R1 | v-r2-r1 / v-r1-r2 | 10.2.1.0/24 |
   | R3 ↔ R1 | v-r3-r1 / v-r1-r3 | 10.3.1.0/24 |
   | R4 ↔ R5 | v-r4-r5 / v-r5-r4 | 10.4.5.0/24 |
   | R5 ↔ R6 | v-r5-r6 / v-r6-r5 | 10.5.6.0/24 |
   | R2 ↔ R3 | v-r2-r3 / v-r3-r2 | 10.2.3.0/24 |

4. **FRR/OSPF configuration** — Writes an `frr.conf` for each router namespace and starts `zebra` and `ospfd` daemons inside each namespace. All inter-router interfaces participate in OSPF area 0. Host-facing interfaces (`v-r4-h1`, `v-r6-h2`) are set to passive so they are advertised into OSPF but do not form adjacencies with hosts.

5. **Convergence wait** — Sleeps 30 seconds after starting FRR to allow OSPF to fully converge before the topology is used.

**Usage:**

```bash
sudo bash new-net-namespace.sh
```

> Requires FRR to be installed (`zebra`, `ospfd`, `vtysh` available in `/usr/lib/frr`).

---

#### `notes.md` — Operations Reference

Contains the IP addressing scheme, hop-by-hop ping commands for verifying connectivity, and useful FRR vtysh commands for inspecting routing state. Also documents the `frr-rX` shell aliases (added to `~/.bashrc`) that simplify accessing the FRR CLI for each router namespace.

**Key aliases (defined in `~/.bashrc`):**

```bash
alias frr-r1='sudo ip netns exec r1 /usr/lib/frr/vtysh -N r1'
# ... r2 through r6 follow the same pattern
```

**Changing OSPF link costs** (to influence routing decisions):

```bash
frr-rX -c "configure terminal" -c "interface v-rX-rY" -c "ip ospf cost Z" -c "end" -c "write memory"
```

---

#### `attempt-to-link.py` — Simulation Replay Script

This Python script reads simulation tick data from the CSV and dynamically applies the corresponding network conditions (delay, jitter, link up/down) to the live namespace topology using Linux Traffic Control (`tc netem`).

**How it works:**

1. **LINK_MAP** — A dictionary mapping every logical satellite link name (e.g. `"Sat1-Sat2"`) to its corresponding namespace and veth interface (e.g. `{"ns": "r1", "iface": "v-r1-r2"}`). This is the bridge between simulation satellite names and real network interfaces.

2. **CSV loading (`load_simulation`)** — Reads the simulation CSV, filters rows by `sim_number`, and organizes data into a dictionary keyed by tick number. Each tick contains a dict of satellite states (altitude, x, y, and which other satellites/hosts it can currently see).

3. **Delay computation** — For each active link at a given tick, `compute_delay_ms` estimates one-way delay using the satellites' altitudes and 2D positions. Jitter is set to 5% of the computed delay via `compute_jitter`.

4. **Applying link states (`apply_tick`)** — For each tick, the script iterates over every entry in `LINK_MAP`:
   - If both endpoints are visible to each other in the current tick's `can_see` data, the link is brought **UP** with computed delay and jitter applied via `tc qdisc netem`.
   - If visibility is lost (satellite has moved out of range), the link is brought **DOWN** by applying 100% packet loss via `tc netem`.

5. **Modes of operation** — On startup, the user selects:
   - **Manual (`m`)** — Steps through ticks one at a time, showing which links will be active and waiting for user confirmation. Useful for debugging or inspecting individual states.
   - **Automatic (`a`)** — Applies ticks sequentially with a configurable delay between each (default: 10 seconds, matching the `TICK_INTERVAL`).

6. **Cleanup** — On exit (including `Ctrl+C`), all `tc` qdiscs are removed from every interface, resetting the topology to its default state.

> **Note:** `DRY_RUN = True` is set by default. In this mode, `tc` commands are printed but not executed. Set `DRY_RUN = False` to apply changes to the live network.

**Usage:**

```bash
# Ensure new-net-namespace.sh has been run first
sudo python3 attempt-to-link.py
```

---

### `simulation/`

This folder contains the Python simulation that models a simplified satellite constellation orbiting a square planet, computing inter-satellite and satellite-to-ground visibility at each time step.

#### `NTN.py` — Main Simulation

**Classes:**

- **`Planet`** — Represents the planet as a square grid. Takes a `size` (side length in km) and a list of three `Orbit` objects. Computes layer areas for L1, L2, and L3 orbital shells as `(size + altitude)²`.

- **`Orbit`** — Defines an orbital shell by altitude (km) and speed (km/s). The `update_position` method moves a satellite along a square clockwise orbit path using a phase offset so that multiple satellites on the same orbit are evenly distributed.

- **`Satellite`** — A node in the network (mapped to a router namespace). Has a name, a list of `Link` objects, an `Orbit`, and a 2D position that is updated each tick.

- **`GroundStation`** — A fixed endpoint (mapped to a host namespace). Has a name and a list of `Link` objects.

- **`Link`** — Represents a logical connection with state (`up`/`down`), bandwidth (Mbps), and latency (ms).

- **`NTN`** — Top-level container holding all satellites, ground stations, and the planet.

**Satellite layout:**

| Satellite | Namespace | Orbit layer | Altitude |
| --------- | --------- | ----------- | -------- |
| Sat1      | r1        | High (L3)   | 15 km    |
| Sat2      | r2        | Medium (L2) | 10 km    |
| Sat3      | r3        | Medium (L2) | 10 km    |
| Sat4      | r4        | Low (L1)    | 5 km     |
| Sat5      | r5        | Low (L1)    | 5 km     |
| Sat6      | r6        | Low (L1)    | 5 km     |

**Visibility logic:**

- `can_see_sat_sat` — Enforces the actual network topology by only allowing links that exist in the namespace setup (e.g. Sat1↔Sat2, Sat1↔Sat3, Sat2↔Sat4, Sat5↔Sat6, etc.) and only when the 2D distance between satellites is within the allowed range for that link type. Cross-layer connections (high↔low) are never allowed.

- `can_see_sat_ground` — Only low-orbit satellites at altitude 5 can reach ground stations. Sat4 can only see Host1 and Sat6 can only see Host2, within a range of 150 km.

**Simulation loop:**

Each tick advances all satellite positions along their orbits, then evaluates visibility for every satellite pair and satellite-to-ground combination. Results are appended to the CSV file.

**Global constants (tunable):**

| Constant               | Default                    | Description                       |
| ---------------------- | -------------------------- | --------------------------------- |
| `ORBIT_SPEED`          | [100, 50, 25] km/s         | Speed per orbital layer           |
| `ORBIT_ALTITUDE`       | [5, 10, 15] km             | Altitude per layer                |
| `TICKS_PER_MINUTE`     | 6                          | Time resolution of the simulation |
| `SIM_DURATION_MINUTES` | 1                          | Total simulation duration         |
| `CSV_PATH`             | `"simulation results.csv"` | Output file                       |

**Usage:**

```bash
python3 NTN.py
```

Each run appends a new simulation (with an auto-incremented `sim_number`) to the CSV.

---

#### `NTN Backup.py` — Original Prototype

An earlier version of the simulation that used simple circular orbits (via `math.cos`/`math.sin`) rather than square orbits, and used simpler range-scaling formulas for visibility rather than topology-enforced rules. Kept for reference.

---

### `ntn_mlm.py` — Link Delay Prediction Model

This script implements the machine learning component of the delay-predictive routing pipeline. It generates its own training data by replaying the orbit simulation with varied parameters, trains a Gradient Boosted Decision Tree regressor entirely in NumPy (no scikit-learn required), and saves a model that can predict one-tick-ahead link delay for all 9 satellite links.

**How it works:**

1. **Data generation** — Rather than relying on the limited rows in `simulation results.csv`, the script re-runs the orbit simulation internally across 30 randomized scenarios (±15% speed variation) for 120 ticks each, producing ~20,000 per-link, per-tick training samples. This variation is necessary because the base orbit is deterministic — plain reruns of `NTN.py` would yield identical positions.

2. **Feature engineering** — For each (link, tick) pair the script extracts 23 features describing the current state of both satellite endpoints: absolute positions (x, y, altitude), relative geometry (distance, angle, Δx/Δy), per-satellite velocity components, approach speed, current link up/down state, current delay, and an encoded link-type label (L3–L2, L2–L2, L2–L1, L1–L1).

3. **Model** — A custom Gradient Boosted Decision Tree regressor built on NumPy. Shallow trees (depth 5) are fitted sequentially to the MSE residuals with a learning rate of 0.08 and 80% row sub-sampling per tree — matching the behaviour of standard gradient boosting libraries.

4. **Evaluation** — Train/test split is by scenario (last 20% held out) to prevent data leakage. Achieved metrics on the held-out set:

   | Metric | Test Value |
   | ------ | ---------- |
   | MAE    | 0.56 ms    |
   | RMSE   | 0.78 ms    |
   | R²     | 0.9983     |

   The dominant features are the relative velocity components (`rel_dy`, `rel_dx`, `approach`), which encode how fast satellites are converging or diverging — physically the most informative signal for near-future delay.

5. **Prediction interface** — `predict_next_tick(sat_states, model, tick)` accepts the same `sat_states` dict that `attempt-to-link.py` already builds from `load_simulation()`, and returns a `{"SatA-SatB": delay_ms}` dict for all 9 links.

6. **Model persistence** — The trained model is saved to `ntn_delay_model.pkl` (~300 KB) using `pickle` for fast reload at inference time.

**CLI options:**

| Flag            | Default | Description                                |
| --------------- | ------- | ------------------------------------------ |
| `--scenarios N` | 30      | Number of simulation scenarios to generate |
| `--ticks N`     | 120     | Ticks per scenario                         |
| `--trees N`     | 100     | Number of boosting estimators              |
| `--depth N`     | 5       | Max tree depth                             |
| `--lr F`        | 0.08    | Gradient boosting learning rate            |
| `--predict`     | —       | Load saved model and run demo inference    |

**Usage:**

```bash
# Train the model (generates data, trains, evaluates, saves ntn_delay_model.pkl)
python ntn_mlm.py

# Larger dataset / more estimators for better accuracy
python ntn_mlm.py --scenarios 50 --ticks 200 --trees 150

# Run demo inference using the saved model
python ntn_mlm.py --predict
```

**Integrating predictions into `attempt-to-link.py`:**

```python
from ntn_mlm import load, predict_next_tick

bundle = load()          # loads ntn_delay_model.pkl
model  = bundle["model"]

# Inside apply_tick(), after loading sat_states for the current tick:
predicted_delays = predict_next_tick(sat_states, model, tick=current_tick)
# predicted_delays = {"Sat1-Sat2": 109.6, "Sat4-Sat5": 44.1, ...}

# Use predicted_delays to pre-emptively adjust OSPF costs for the next tick
# before tc netem applies the actual conditions.
```

---

### `simulation results.csv` — Simulation Output Data

The CSV is the data interface between the simulation and the network replay script. Each row represents one satellite's state at one simulation tick.

**Schema:**

| Column           | Type    | Description                                                     |
| ---------------- | ------- | --------------------------------------------------------------- |
| `sim_number`     | Integer | Identifies which simulation run produced this row               |
| `time_s`         | Float   | Elapsed simulation time in seconds                              |
| `tick`           | Integer | Tick index (0-based); each tick is 10 seconds of real time      |
| `sat_name`       | String  | Satellite name (`Sat1`–`Sat6`)                                  |
| `orbit_altitude` | Integer | Orbital altitude in km (5, 10, or 15)                           |
| `orbit_speed`    | Integer | Orbital speed in km/s                                           |
| `x`              | Float   | Satellite X position on the simulation grid                     |
| `y`              | Float   | Satellite Y position on the simulation grid                     |
| `can_see`        | String  | Comma-separated list of satellites/hosts visible from this node |

The `can_see` column is what `attempt-to-link.py` reads to determine which links should be UP or DOWN at each tick, and the `x`, `y`, and `orbit_altitude` columns are used to compute the delay to apply to active links.

---

#### `simulation_viewer.html` — Visual Playback Tool

A self-contained HTML/JS tool for visually inspecting simulation CSV output in a browser. No server required — open the file directly.

**Features:**

- Load a `simulation results.csv` file via the file picker
- Visualizes the square planet grid with the three orbital layers (L1, L2, L3) drawn as nested squares
- Plots each satellite's position at the selected tick on a 2D canvas
- Step forward/backward through ticks manually or scrub with a slider
- Configurable grid size and layer dimensions to match the values used when the simulation was run

**Usage:**

1. Run `NTN.py` to generate a `simulation results.csv`
2. Open `simulation_viewer.html` in a browser
3. Click "Load simulation results CSV" and select the file
4. Use the slider or step buttons to walk through each tick

#### `ntn_dashboard.py` - Visual Delay & Link status

# Print formatted table for every tick

python3 ntn_dashboard.py --cli

# Single tick snapshot

python3 ntn_dashboard.py --cli --tick 3

# Filter to one sim run + one tick

python3 ntn_dashboard.py --cli --sim 1 --tick 3

# Open the full interactive GUI (matplotlib)

## python3 ntn_dashboard.py

## End-to-End Workflow

```
1. sudo bash namespace-network/new-net-namespace.sh
      └─ Creates namespaces r1–r6, h1–h2, starts FRR/OSPF, waits for convergence

2. python3 simulation/NTN.py
      └─ Runs satellite orbit simulation, appends results to simulation results.csv

3. (Optional) Open simulation_viewer.html in browser to verify simulation visually

4. python3 ntn_mlm.py
      └─ Generates training data internally (no extra CSV runs needed)
      └─ Trains Gradient Boosted regressor on ~20,000 link-tick samples
      └─ Evaluates model (MAE, RMSE, R²) and saves ntn_delay_model.pkl

5. sudo python3 namespace-network/attempt-to-link.py
      └─ Reads CSV, replays tick-by-tick, applies tc netem delay/loss to namespaces
      └─ (Optional) import predict_next_tick from ntn_mlm to enable predictive routing
      └─ Choose manual or automatic mode
      └─ Topology is reset to clean state on exit
```

---

## Prerequisites

- Linux host with root/sudo access
- [FRR](https://frrouting.org/) installed (`zebra`, `ospfd`, `vtysh` in `/usr/lib/frr`)
- Python 3.x with `numpy` and `pandas` (`pip install numpy pandas`)
- `iproute2` with `tc` and `netem` support (`sch_netem` kernel module)
- `traceroute` (optional, for path verification)

> **Note:** `ntn_mlm.py` has no dependency on scikit-learn or any other ML library — it implements Gradient Boosted Decision Trees from scratch using only NumPy.

---

## Previous Approach

The initial approach used Mininet with an OpenDaylight (ODL) SDN controller. While functional at a base level, it proved difficult to inject delay, jitter, or other impairments into the live topology without causing L2 loops, and telemetry collection was cumbersome. The project pivoted to Linux network namespaces + FRR, which offers finer-grained control over link conditions and is more suitable for the delay-injection and telemetry needs of this project.
