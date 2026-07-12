"""
Debugging Knowledge Base and Pattern Recognition

Manages known failure patterns and component-specific failure modes, matched
against observed symptoms (Jaccard similarity). Ported verbatim from
circuit-synth ``debugging/knowledge_base.py`` -- the content is DSL-agnostic
(symptoms, root causes, solutions, measurements), so nothing here couples to the
authoring DSL. One library-friendliness change: the default database is
**in-memory** (``:memory:``) so importing/using it does not write a
``memory-bank/`` tree into the caller's cwd; pass ``db_path`` to persist.
"""

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class DebugPattern:
    """Represents a known debugging pattern from historical data"""

    pattern_id: str
    category: str
    symptoms: List[str]
    root_cause: str
    solutions: List[str]
    component_types: List[str]
    occurrence_count: int = 1
    success_rate: float = 1.0
    typical_measurements: Dict[str, Any] = None
    references: List[str] = None

    def matches_symptoms(self, symptoms: List[str], threshold: float = 0.5) -> float:
        """Calculate similarity between pattern and given symptoms"""
        if not self.symptoms or not symptoms:
            return 0.0

        # Simple Jaccard similarity
        pattern_set = set(
            word.lower() for symptom in self.symptoms for word in symptom.split()
        )
        symptom_set = set(
            word.lower() for symptom in symptoms for word in symptom.split()
        )

        intersection = len(pattern_set & symptom_set)
        union = len(pattern_set | symptom_set)

        return intersection / union if union > 0 else 0.0


@dataclass
class ComponentFailure:
    """Represents known failure modes for specific components"""

    component_type: str  # e.g., "AMS1117-3.3"
    manufacturer: str
    failure_mode: str
    failure_rate: float  # Failures per million hours
    symptoms: List[str]
    root_causes: List[str]
    environmental_factors: List[str]  # Temperature, humidity, vibration
    mitigation: List[str]
    references: List[str] = None


class DebugKnowledgeBase:
    """Manages debugging knowledge and historical patterns"""

    def __init__(self, db_path: Optional[Path] = None):
        # Default to an in-memory DB so a lookup does not litter the cwd; pass a
        # path to persist recorded sessions across runs.
        if db_path is None:
            self.db_path = ":memory:"
        else:
            self.db_path = str(db_path)
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = None
        self._init_database()
        self._load_default_patterns()

    def close(self):
        """Close the SQLite connection.

        Important on Windows: an open connection keeps the .db file locked, so
        callers using a temporary directory must close before it is removed
        (otherwise cleanup raises PermissionError [WinError 32]).
        """
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def _init_database(self):
        """Initialize SQLite database for pattern storage"""
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row

        # Create tables
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS debug_patterns (
                pattern_id TEXT PRIMARY KEY,
                category TEXT,
                symptoms TEXT,  -- JSON array
                root_cause TEXT,
                solutions TEXT,  -- JSON array
                component_types TEXT,  -- JSON array
                occurrence_count INTEGER DEFAULT 1,
                success_rate REAL DEFAULT 1.0,
                typical_measurements TEXT,  -- JSON object
                reference_docs TEXT,  -- JSON array
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS component_failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                component_type TEXT,
                manufacturer TEXT,
                failure_mode TEXT,
                failure_rate REAL,
                symptoms TEXT,  -- JSON array
                root_causes TEXT,  -- JSON array
                environmental_factors TEXT,  -- JSON array
                mitigation TEXT,  -- JSON array
                reference_docs TEXT,  -- JSON array
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS debug_sessions (
                session_id TEXT PRIMARY KEY,
                board_name TEXT,
                board_version TEXT,
                symptoms TEXT,  -- JSON array
                measurements TEXT,  -- JSON object
                root_cause TEXT,
                resolution TEXT,
                duration_minutes INTEGER,
                success BOOLEAN,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE INDEX IF NOT EXISTS idx_patterns_category ON debug_patterns(category);
            CREATE INDEX IF NOT EXISTS idx_components_type ON component_failures(component_type);
            CREATE INDEX IF NOT EXISTS idx_sessions_board ON debug_sessions(board_name);
        """)
        self.conn.commit()

    def _load_default_patterns(self):
        """Load default debugging patterns"""
        default_patterns = [
            DebugPattern(
                pattern_id=self._generate_pattern_id(["3.3V", "low", "regulator"]),
                category="power",
                symptoms=[
                    "3.3V rail reading low",
                    "Board draws excessive current",
                    "Regulator hot",
                ],
                root_cause="Overloaded voltage regulator",
                solutions=[
                    "Replace regulator with higher current rating",
                    "Add heat sink to regulator",
                    "Distribute load across multiple regulators",
                    "Reduce circuit current consumption",
                ],
                component_types=["AMS1117", "LM1117", "LD1117"],
                typical_measurements={
                    "3.3V_rail": 2.8,
                    "regulator_temp_c": 85,
                    "current_draw_ma": 1200,
                },
            ),
            DebugPattern(
                pattern_id=self._generate_pattern_id(["LLC", "output", "low"]),
                category="power",
                symptoms=[
                    "LLC output voltage low",
                    "Resonant converter output below target",
                    "Half-bridge converter gain wrong",
                    "MOSFETs hot / hard switching",
                ],
                root_cause=(
                    "LLC operating point / timing: switching frequency above the "
                    "resonant fr (buck region -> low gain), deadtime too short for "
                    "ZVS (hard switching, hot FETs), or the magnetizing/turns "
                    "ratio set wrong"
                ),
                solutions=[
                    "Lower FSW toward/below fr = 1/(2*pi*sqrt(Lr*Cr)) to raise gain",
                    "Check Vout ~= n*(Vin/2) - Vf at resonance; fix the turns ratio n",
                    "Size the deadtime so V(sw) swings rail-to-rail before the "
                    "opposite gate rises (ZVS); too short -> hard switching",
                    "Verify Lm (transformer primary LP) and Lr/Cr give the intended "
                    "fr and Ln = Lm/Lr",
                ],
                component_types=["Transformer_1P_SS", "MOSFET", "HALFBRIDGE"],
                typical_measurements={
                    "vout_v": 8.0,
                    "vds_at_turnon_v": 48.0,
                    "fsw_khz": 150,
                },
            ),
            DebugPattern(
                pattern_id=self._generate_pattern_id(["ZVS", "hard", "switching"]),
                category="power",
                symptoms=[
                    "expected ZVS, measured hard switching",
                    "hard switching below resonance",
                    "ZVS lost at full load",
                    "switch node not swinging rail to rail during deadtime",
                ],
                root_cause=(
                    "ZVS is LOAD-dependent, not a fixed frequency band: a heavier "
                    "load raises the tank Q and pushes the ZVS boundary toward "
                    "resonance, so an operating point that soft-switched at light "
                    "load hard-switches at full load (a current-phase effect -- "
                    "textbook physics, not a tooling failure). Only after ruling "
                    "that out: fsw too far below fr for the tank, or deadtime "
                    "mismatched to the actual V(sw) swing time."
                ),
                solutions=[
                    "Check the load first: recompute/re-simulate at the ACTUAL "
                    "load Q -- at higher load the ZVS region shrinks toward fr "
                    "(e.g. ~0.75*fr soft at ~12 W can hard-switch at 40 W until "
                    "~0.9*fr); deadtime sweeps won't fix a load-Q loss",
                    "Move fsw closer to fr (keep the tank inductive) and re-check",
                    "Only then tune deadtime so V(sw) completes the swing before "
                    "the opposite gate rises",
                    "Measure Vds JUST BEFORE each gate edge on a settled tail of "
                    "a fine .tran (see canaries/llc_resonant/zvs_metric.py) -- "
                    "coarse or early-cycle sampling reports phantom hard switching",
                ],
                component_types=["MOSFET", "HALFBRIDGE", "Transformer_1P_SS"],
                typical_measurements={
                    "vds_at_turnon_v": 30.0,
                    "load_w": 40,
                    "fsw_over_fr": 0.75,
                },
            ),
            DebugPattern(
                pattern_id=self._generate_pattern_id(
                    ["oscillator", "wont", "start"]),
                category="power",
                symptoms=[
                    "oscillator won't start",
                    "timestep too small at t=0",
                    "self-oscillating converter never starts",
                    "singular matrix at start of transient",
                    "Timestep too small; time=1e-18",
                ],
                root_cause=(
                    "A symmetric self-oscillating (Royer/Mazzilli ZVS) converter "
                    "will NOT start from a clean DC point, and any degenerate/"
                    "floating node makes ngspice die at t=0: (a) no asymmetric "
                    ".ic kick; (b) the gate seed is above the rail at low VBUS; "
                    "(c) an isolated transformer winding lacks a DC path to node 0 "
                    "(a `gnd += other_net` merge left a degenerate node)."
                ),
                solutions=[
                    "Seed an asymmetric .ic (one gate high, opposite drain at "
                    "VBUS) with stiff=True + use_initial_condition=True",
                    "Clamp the gate seed to min(clamp_voltage, VBUS) so a "
                    "low-VBUS sweep point doesn't seed above the rail",
                    "Tie every isolated winding directly to the GND net object "
                    "(node 0), not via a separate net merged into GND -- the only "
                    "symptom of the degenerate node is 'singular matrix: check "
                    "node <net>'",
                    "See canaries/royer_zvs/ for the working start-up recipe",
                ],
                component_types=["Transformer_1P_SS", "MOSFET", "Royer", "ZVS"],
                typical_measurements={"t_fail_s": 6e-18, "vbus_v": 6.0},
            ),
            DebugPattern(
                pattern_id=self._generate_pattern_id(
                    ["oscillator", "parasitic", "frequency"]),
                category="power",
                symptoms=[
                    "self-oscillator runs at 10-100x the designed frequency",
                    "oscillation frequency far above LC estimate",
                    "small-amplitude high-frequency limit cycle",
                    "measured f_osc doesn't match the tank",
                ],
                root_cause=(
                    "The tank capacitor (Cres) is below its stability floor "
                    "(~10 nF in the worked example), so the oscillator jumps to a "
                    "parasitic ~MHz mode with small amplitude instead of the "
                    "intended ~tens-of-kHz resonant tank mode."
                ),
                solutions=[
                    "Raise Cres back above the floor; raise f_osc by LOWERING the "
                    "winding inductance (LP) instead of shrinking Cres",
                    "Sanity-check the measured f_osc against 1/(2*pi*sqrt(L*C)); "
                    "a 10x+ mismatch means a parasitic mode, not the tank",
                    "See canaries/royer_zvs/ for the LP-vs-Cres tuning",
                ],
                component_types=["Transformer_1P_SS", "MOSFET", "Royer", "ZVS"],
                typical_measurements={"f_osc_khz": 2041, "cres_nf": 2.7},
            ),
            DebugPattern(
                pattern_id=self._generate_pattern_id(
                    ["operating", "point", "midrange"]),
                category="power",
                symptoms=[
                    "DC operating point fails only at some setpoints",
                    "operating point fails at mid setpoints",
                    "op point converges at extremes but not mid-range",
                    "No convergence in dc analysis",
                    "Command run failed at mid setpoint",
                ],
                root_cause=(
                    "A stiff vendor MOSFET subckt (e.g. IRF740/POWMOS.LIB) inside a "
                    "high-gain (Aol~1e6) DC control loop: the Newton solver has no "
                    "basin at mid-range setpoints even though the extremes converge. "
                    "It is a solver-basin problem, NOT a Vds boundary or a design "
                    "bug -- post-fix the surfaced error carries the ngspice tail "
                    "('No convergence in dc analysis')."
                ),
                solutions=[
                    "UIC transient settle: seed loop nodes at 0 with "
                    "use_initial_condition=True, let the loop charge a small "
                    "(~10 nF) output cap for ~20 ms, average the settled tail",
                    "Use .op only at the setpoints where it converges (there it is "
                    "sub-mV exact); tail-average elsewhere",
                    "NEVER add a large conditioning resistor across the pass device "
                    "to aid convergence -- it injects current and corrupts Vout "
                    "(measured 12 V -> 13.3 V / 29 V with 10 MOhm / 1 MOhm)",
                ],
                component_types=["IRF740", "MOSFET", "subckt", "vendor_lib"],
                typical_measurements={"aol": 1e6, "settle_ms": 20},
            ),
            DebugPattern(
                pattern_id=self._generate_pattern_id(
                    ["timestep", "dmos", "commutation"]),
                category="power",
                symptoms=[
                    "timestep too small",
                    "trouble with dmos-instance",
                    "run aborted in switching transient",
                    "Timestep too small; trouble with xq1:dmos-instance",
                    "transient aborts on hard commutation",
                ],
                root_cause=(
                    "A vendor MOSFET subckt's internal capacitances under hard "
                    "commutation stall the integrator (the reason now appears in "
                    "the surfaced ngspice error tail)."
                ),
                solutions=[
                    "Add an RC snubber across the switch node (start ~100 Ohm + "
                    "680 pF) to tame dv/dt",
                    "Add a gate series resistor and slow the edges (~200 ns)",
                    "Lower fsw; run transient_analysis(stiff=True, "
                    "use_initial_condition=True)",
                    "When Rload*Cout >> the runnable window, seed the rail near its "
                    "expected steady state in initial_conditions and tail-average",
                    "Sanity-check: converged != correct -- compare settled Vout "
                    "against Vin/(1-D)",
                ],
                component_types=["IRF740", "MOSFET", "subckt", "boost"],
                typical_measurements={"snubber_ohm": 100, "snubber_pf": 680},
            ),
            DebugPattern(
                pattern_id=self._generate_pattern_id(["USB", "enumeration", "fail"]),
                category="digital",
                symptoms=[
                    "USB device not recognized",
                    "Enumeration fails",
                    "Device descriptor error",
                ],
                root_cause="USB differential pair signal integrity issue",
                solutions=[
                    "Match D+ and D- trace lengths within 0.1mm",
                    "Maintain 90Ω differential impedance",
                    "Add common mode choke",
                    "Verify crystal frequency (12MHz, 24MHz, or 48MHz)",
                    "Add 22-33Ω series resistors on D+ and D-",
                ],
                component_types=["USB_Connector", "Crystal", "STM32", "ESP32"],
                typical_measurements={"D+_voltage": 0, "D-_voltage": 0, "VBUS": 5.0},
            ),
            DebugPattern(
                pattern_id=self._generate_pattern_id(["I2C", "no", "ACK"]),
                category="digital",
                symptoms=["I2C NACK", "No ACK from slave", "I2C timeout"],
                root_cause="I2C pull-up resistor issue",
                solutions=[
                    "Add 2.2kΩ to 10kΩ pull-up resistors on SDA and SCL",
                    "Verify I2C address (use I2C scanner)",
                    "Check voltage levels match between master and slave",
                    "Reduce I2C clock speed",
                    "Ensure proper ground connection between devices",
                ],
                component_types=["I2C_Device", "Microcontroller", "Sensor"],
                typical_measurements={
                    "SDA_high": 3.3,
                    "SCL_high": 3.3,
                    "pullup_resistance": 4700,
                },
            ),
            DebugPattern(
                pattern_id=self._generate_pattern_id(
                    ["oscillation", "power", "unstable"]
                ),
                category="power",
                symptoms=[
                    "Power rail oscillating",
                    "Unstable output voltage",
                    "Audible noise from regulator",
                ],
                root_cause="Incorrect output capacitor ESR",
                solutions=[
                    "Use capacitor with ESR in regulator's stable range",
                    "Add 10μF ceramic in parallel with electrolytic",
                    "Check PCB layout for long feedback traces",
                    "Add feedforward capacitor in feedback network",
                ],
                component_types=["LDO", "Buck_Converter", "Boost_Converter"],
                typical_measurements={"oscillation_freq_khz": 50, "ripple_vpp": 0.5},
            ),
            DebugPattern(
                pattern_id=self._generate_pattern_id(["ESD", "damage", "input"]),
                category="power",
                symptoms=[
                    "Component fails after handling",
                    "Intermittent failures",
                    "Input protection damaged",
                ],
                root_cause="ESD damage to semiconductor",
                solutions=[
                    "Add TVS diodes on all external interfaces",
                    "Implement proper ESD protection (IEC 61000-4-2)",
                    "Add series resistors to limit current",
                    "Use ESD-protected components",
                    "Ensure proper chassis grounding",
                ],
                component_types=["MOSFET", "IC", "Connector"],
                typical_measurements={
                    "input_impedance": "open",
                    "leakage_current_ua": 1000,
                },
            ),
            # --- small-signal amplifier patterns (added after the DiffAmp E2E; B6)
            DebugPattern(
                pattern_id=self._generate_pattern_id(
                    ["opamp", "output", "stuck", "zero", "gain"]
                ),
                category="analog",
                symptoms=[
                    "Op-amp output stuck at zero",
                    "Output stuck at zero",
                    "Differential gain measured 0",
                    "Gain measured 0",
                    "Gain far below design",
                    "DC gain wrong but AC gain correct",
                ],
                root_cause=(
                    "Wrong DC source polarity/value, an open feedback path, or a "
                    "saturated stage (a DC-only error that AC linearization hides)"
                ),
                solutions=[
                    "Check every DC source's sign AND magnitude actually reaches "
                    "the node (a silent sign/value drop reads +V for -V)",
                    "Verify the feedback network closes (in- to output through Rf)",
                    "Confirm no stage is railed: check each op-amp output vs its "
                    "supply rails at the DC operating point",
                    "Compare the DC result against the AC result -- if AC is right "
                    "and DC is wrong, suspect a source-emission or bias error",
                ],
                component_types=["Op-Amp", "ADA4807", "Amplifier_Operational"],
                typical_measurements={"vout_dc": 0.0, "vout_ac_db": -14.0},
            ),
            DebugPattern(
                pattern_id=self._generate_pattern_id(
                    ["dc", "ac", "inconsistent", "gain"]
                ),
                category="analog",
                symptoms=[
                    "DC result inconsistent with AC result",
                    "AC bandwidth correct but DC gain wrong",
                    "Transient offset but AC transfer fine",
                ],
                root_cause=(
                    "DC source sign/value emission error (the DC operating point "
                    "uses the literal source value; AC linearizes around it)"
                ),
                solutions=[
                    "Dump the SPICE netlist and read the source card's sign/value",
                    "Test the source alone across a resistor to ground and confirm "
                    "the node sits at the expected signed voltage",
                    "Prefer signed values over pin-swap workarounds once the "
                    "simulator honors them",
                ],
                component_types=["VDC", "IDC", "Simulation_SPICE"],
                typical_measurements={"vnode_expected": -1.0, "vnode_measured": 1.0},
            ),
            DebugPattern(
                pattern_id=self._generate_pattern_id(
                    ["cmrr", "poor", "common", "mode", "rejection"]
                ),
                category="analog",
                symptoms=[
                    "CMRR poor",
                    "Common-mode rejection low",
                    "Output moves with common-mode input",
                    "Difference amplifier not rejecting common mode",
                ],
                root_cause=(
                    "Difference-network resistor mismatch or single-ended drive "
                    "asymmetry -- CMRR is set by resistor matching"
                ),
                solutions=[
                    "Match the four difference-amp resistors (0.1% or better); "
                    "CMRR ~= 20*log10(gain / relative_mismatch)",
                    "Drive both inputs symmetrically for the CM test",
                    "Check the reference/ground return of the difference stage",
                    "Verify the two input series resistors are equal",
                ],
                component_types=[
                    "Op-Amp",
                    "Instrumentation_Amplifier",
                    "Amplifier_Operational",
                ],
                typical_measurements={"cmrr_db": 20.0, "resistor_mismatch_pct": 1.0},
            ),
            DebugPattern(
                pattern_id=self._generate_pattern_id(
                    ["gain", "peaking", "oscillation", "opamp", "bandwidth"]
                ),
                category="analog",
                symptoms=[
                    "Gain peaking near the corner frequency",
                    "Amplifier oscillation",
                    "Ringing on the step response",
                    "AC response peaks before roll-off",
                ],
                root_cause=(
                    "Finite gain-bandwidth product interacting with source/feedback "
                    "capacitance (insufficient phase margin)"
                ),
                solutions=[
                    "Add a small feedback capacitor (Cf) across Rf to compensate",
                    "Reduce the source/input capacitance seen at the summing node",
                    "Model the real GBW (Sim_Gbw) rather than an ideal op-amp so "
                    "the peaking is visible in simulation",
                    "Lower the closed-loop gain or pick a higher-GBW part",
                ],
                component_types=["Op-Amp", "ADA4807", "Amplifier_Operational"],
                typical_measurements={"peaking_db": 6.0, "gbw_hz": 180e6},
            ),
        ]

        for pattern in default_patterns:
            self.add_pattern(pattern)

    def _generate_pattern_id(self, keywords: List[str]) -> str:
        """Generate unique pattern ID from keywords"""
        text = "_".join(sorted(keywords)).lower()
        return hashlib.md5(text.encode()).hexdigest()[:12]

    def add_pattern(self, pattern: DebugPattern) -> bool:
        """Add or update a debugging pattern"""
        try:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO debug_patterns 
                (pattern_id, category, symptoms, root_cause, solutions, component_types,
                 occurrence_count, success_rate, typical_measurements, reference_docs, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
                (
                    pattern.pattern_id,
                    pattern.category,
                    json.dumps(pattern.symptoms),
                    pattern.root_cause,
                    json.dumps(pattern.solutions),
                    json.dumps(pattern.component_types),
                    pattern.occurrence_count,
                    pattern.success_rate,
                    (
                        json.dumps(pattern.typical_measurements)
                        if pattern.typical_measurements
                        else None
                    ),
                    json.dumps(pattern.references) if pattern.references else None,
                ),
            )
            self.conn.commit()
            return True
        except Exception as e:
            print(f"Error adding pattern: {e}")
            return False

    def search_patterns(
        self,
        symptoms: List[str],
        category: Optional[str] = None,
        min_similarity: float = 0.3,
    ) -> List[Tuple[DebugPattern, float]]:
        """Search for patterns matching given symptoms"""
        query = "SELECT * FROM debug_patterns"
        params = []

        if category:
            query += " WHERE category = ?"
            params.append(category)

        cursor = self.conn.execute(query, params)
        matches = []

        for row in cursor:
            pattern = DebugPattern(
                pattern_id=row["pattern_id"],
                category=row["category"],
                symptoms=json.loads(row["symptoms"]),
                root_cause=row["root_cause"],
                solutions=json.loads(row["solutions"]),
                component_types=json.loads(row["component_types"]),
                occurrence_count=row["occurrence_count"],
                success_rate=row["success_rate"],
                typical_measurements=(
                    json.loads(row["typical_measurements"])
                    if row["typical_measurements"]
                    else None
                ),
                references=(
                    json.loads(row["reference_docs"]) if row["reference_docs"] else None
                ),
            )

            similarity = pattern.matches_symptoms(symptoms)
            if similarity >= min_similarity:
                matches.append((pattern, similarity))

        # Sort by similarity and success rate
        matches.sort(key=lambda x: (x[1], x[0].success_rate), reverse=True)
        return matches

    def add_component_failure(self, failure: ComponentFailure) -> bool:
        """Add known component failure mode"""
        try:
            self.conn.execute(
                """
                INSERT INTO component_failures 
                (component_type, manufacturer, failure_mode, failure_rate, symptoms,
                 root_causes, environmental_factors, mitigation, reference_docs)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    failure.component_type,
                    failure.manufacturer,
                    failure.failure_mode,
                    failure.failure_rate,
                    json.dumps(failure.symptoms),
                    json.dumps(failure.root_causes),
                    json.dumps(failure.environmental_factors),
                    json.dumps(failure.mitigation),
                    json.dumps(failure.references) if failure.references else None,
                ),
            )
            self.conn.commit()
            return True
        except Exception as e:
            print(f"Error adding component failure: {e}")
            return False

    def get_component_failures(self, component_type: str) -> List[ComponentFailure]:
        """Get known failure modes for a component type"""
        cursor = self.conn.execute(
            "SELECT * FROM component_failures WHERE component_type LIKE ?",
            (f"%{component_type}%",),
        )

        failures = []
        for row in cursor:
            failure = ComponentFailure(
                component_type=row["component_type"],
                manufacturer=row["manufacturer"],
                failure_mode=row["failure_mode"],
                failure_rate=row["failure_rate"],
                symptoms=json.loads(row["symptoms"]),
                root_causes=json.loads(row["root_causes"]),
                environmental_factors=json.loads(row["environmental_factors"]),
                mitigation=json.loads(row["mitigation"]),
                references=(
                    json.loads(row["reference_docs"]) if row["reference_docs"] else None
                ),
            )
            failures.append(failure)

        return failures

    def record_debug_session(self, session_data: Dict[str, Any]) -> bool:
        """Record a completed debugging session for future reference"""
        try:
            self.conn.execute(
                """
                INSERT INTO debug_sessions 
                (session_id, board_name, board_version, symptoms, measurements,
                 root_cause, resolution, duration_minutes, success)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    session_data["session_id"],
                    session_data["board_name"],
                    session_data.get("board_version", "1.0"),
                    json.dumps(session_data.get("symptoms", [])),
                    json.dumps(session_data.get("measurements", {})),
                    session_data.get("root_cause", ""),
                    session_data.get("resolution", ""),
                    session_data.get("duration_minutes", 0),
                    session_data.get("success", False),
                ),
            )
            self.conn.commit()

            # Update pattern statistics if similar pattern exists
            if session_data.get("success") and session_data.get("symptoms"):
                patterns = self.search_patterns(session_data["symptoms"])
                if patterns:
                    best_pattern = patterns[0][0]
                    self.conn.execute(
                        """
                        UPDATE debug_patterns 
                        SET occurrence_count = occurrence_count + 1,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE pattern_id = ?
                    """,
                        (best_pattern.pattern_id,),
                    )
                    self.conn.commit()

            return True
        except Exception as e:
            print(f"Error recording session: {e}")
            return False

    def get_similar_sessions(
        self, board_name: str, symptoms: List[str], limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Find similar debugging sessions from history"""
        # First try exact board matches
        cursor = self.conn.execute(
            "SELECT * FROM debug_sessions WHERE board_name = ? ORDER BY created_at DESC LIMIT ?",
            (board_name, limit * 2),
        )

        sessions = []
        for row in cursor:
            session_symptoms = json.loads(row["symptoms"])
            # Simple similarity check
            if any(s in " ".join(symptoms) for s in session_symptoms):
                sessions.append(
                    {
                        "session_id": row["session_id"],
                        "board_name": row["board_name"],
                        "symptoms": session_symptoms,
                        "root_cause": row["root_cause"],
                        "resolution": row["resolution"],
                        "success": row["success"],
                        "created_at": row["created_at"],
                    }
                )

        return sessions[:limit]
