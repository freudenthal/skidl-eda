"""
Debugging Knowledge Base and Pattern Recognition

Known failure patterns (symptoms -> root cause -> solutions) matched against
observed symptoms by Jaccard word-overlap, plus a per-part store of **SPICE model
reliability** notes.

Content is **data, not code**: patterns live in ``data/*.jsonl`` (see
``data/README.md``), curated ruthlessly to the project/simulator-specific traps an
LLM does not already know. The loader merges a bundled seed with an optional
``.claude/memory`` overlay so a run can append a newly discovered trap without a
code change. The SQLite layer is an in-memory search index rebuilt from the JSONL
each construction -- there is no persisted "learning" table (that machinery in the
circuit-synth original never persisted in the default path and was removed).

Origin: ported from circuit-synth ``debugging/knowledge_base.py``; the DSL-agnostic
content survives, the dead session-recording layer does not.
"""

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Bundled, version-controlled seed data (curated + pruned).
_DATA_DIR = Path(__file__).with_name("data")
_PATTERNS_FILE = "debug_patterns.jsonl"
_SPICE_FILE = "spice_model_reliability.jsonl"


def resolve_memory_dir(memory_dir: Optional[Path] = None) -> Optional[Path]:
    """Resolve the appendable ``.claude/memory`` overlay directory.

    Order: explicit arg -> ``$SKIDL_EDA_MEMORY_DIR`` -> walk up from cwd for a
    ``.claude/`` dir and use ``<that>/.claude/memory`` -> ``None`` (seed-only).
    Never creates anything; a missing dir simply means seed-only.
    """
    if memory_dir is not None:
        return Path(memory_dir)
    env = os.environ.get("SKIDL_EDA_MEMORY_DIR")
    if env:
        return Path(env)
    here = Path.cwd().resolve()
    for parent in (here, *here.parents):
        if (parent / ".claude").is_dir():
            return parent / ".claude" / "memory"
    return None


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:  # skip a bad line, keep the rest
                print(f"knowledge_base: skipping {path.name}:{lineno}: {e}")
    return rows


def _load_records(filename: str, memory_dir: Optional[Path]) -> List[Dict[str, Any]]:
    """Seed records overlaid by any same-named file under ``memory_dir``.

    Overlay entries with a matching key (``id`` for patterns, ``part`` for spice
    notes) replace the seed entry; new keys are appended.
    """
    key = "part" if filename == _SPICE_FILE else "id"
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for rec in _read_jsonl(_DATA_DIR / filename):
        k = rec.get(key)
        if k is None:
            continue
        if k not in merged:
            order.append(k)
        merged[k] = rec
    if memory_dir is not None:
        for rec in _read_jsonl(Path(memory_dir) / filename):
            k = rec.get(key)
            if k is None:
                continue
            if k not in merged:
                order.append(k)
            merged[k] = rec
    return [merged[k] for k in order]


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

    @classmethod
    def from_record(cls, rec: Dict[str, Any]) -> "DebugPattern":
        """Build a pattern from a ``debug_patterns.jsonl`` record."""
        return cls(
            pattern_id=str(rec["id"]),
            category=rec.get("category", "general"),
            symptoms=list(rec.get("symptoms", [])),
            root_cause=rec.get("root_cause", ""),
            solutions=list(rec.get("solutions", [])),
            component_types=list(rec.get("component_types", [])),
            occurrence_count=int(rec.get("occurrence_count", 1)),
            success_rate=float(rec.get("success_rate", 1.0)),
            typical_measurements=rec.get("measurements") or rec.get("typical_measurements"),
            references=rec.get("references"),
        )


@dataclass
class SpiceModelNote:
    """Reliability note for one SPICE model in the corpus."""

    part: str
    kind: str
    status: str  # ok | conditional | avoid
    trap: str
    detect: str = ""
    workaround: str = ""
    source: str = ""
    see: List[str] = None

    @classmethod
    def from_record(cls, rec: Dict[str, Any]) -> "SpiceModelNote":
        return cls(
            part=str(rec["part"]),
            kind=rec.get("kind", ""),
            status=rec.get("status", "conditional"),
            trap=rec.get("trap", ""),
            detect=rec.get("detect", ""),
            workaround=rec.get("workaround", ""),
            source=rec.get("source", ""),
            see=list(rec.get("see", []) or []),
        )

    def to_pattern(self) -> DebugPattern:
        """Surface this note through the symptom search as a ``spice_model`` pattern."""
        symptoms = [f"{self.part} {self.kind}".strip(), self.trap]
        if self.detect:
            symptoms.append(self.detect)
        solutions = [s for s in (self.workaround, f"model status: {self.status}") if s]
        return DebugPattern(
            pattern_id=f"spice::{self.part}",
            category="spice_model",
            symptoms=symptoms,
            root_cause=f"[{self.status}] {self.part}: {self.trap}",
            solutions=solutions,
            component_types=[self.part, self.kind],
            references=self.see,
        )


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
    """Symptom -> pattern search over the curated JSONL knowledge base.

    The knowledge is data (``data/*.jsonl`` seed + optional ``.claude/memory``
    overlay); this class loads it into an **in-memory** SQLite index for search.
    Nothing is written to disk -- to add a lesson, append a line to the overlay
    JSONL (see ``data/README.md``), not to a DB.
    """

    def __init__(
        self,
        memory_dir: Optional[Path] = None,
        *,
        load_seed: bool = True,
    ):
        self.memory_dir = resolve_memory_dir(memory_dir)
        self.conn = None
        self._spice_notes: List[SpiceModelNote] = []
        self._init_database()
        if load_seed:
            self._load_seed()

    def close(self):
        """Close the SQLite connection (the in-memory index is discarded)."""
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def _init_database(self):
        """Initialize the in-memory SQLite search index."""
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
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
                reference_docs TEXT  -- JSON array
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
                reference_docs TEXT  -- JSON array
            );

            CREATE INDEX IF NOT EXISTS idx_patterns_category ON debug_patterns(category);
            CREATE INDEX IF NOT EXISTS idx_components_type ON component_failures(component_type);
        """)
        self.conn.commit()

    def _load_seed(self):
        """Load the curated patterns + SPICE-model notes (seed overlaid by memory)."""
        for rec in _load_records(_PATTERNS_FILE, self.memory_dir):
            try:
                self.add_pattern(DebugPattern.from_record(rec))
            except (KeyError, TypeError) as e:
                print(f"knowledge_base: bad pattern record {rec.get('id')!r}: {e}")
        for rec in _load_records(_SPICE_FILE, self.memory_dir):
            try:
                note = SpiceModelNote.from_record(rec)
            except (KeyError, TypeError) as e:
                print(f"knowledge_base: bad spice record {rec.get('part')!r}: {e}")
                continue
            self._spice_notes.append(note)
            # also index it as a searchable pattern
            self.add_pattern(note.to_pattern())

    def add_pattern(self, pattern: DebugPattern) -> bool:
        """Add or update a debugging pattern in the in-memory index."""
        try:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO debug_patterns
                (pattern_id, category, symptoms, root_cause, solutions, component_types,
                 occurrence_count, success_rate, typical_measurements, reference_docs)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        params: List[Any] = []

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

    # ---- SPICE model reliability -------------------------------------------

    def spice_model_notes(
        self, part: Optional[str] = None, status: Optional[str] = None
    ) -> List[SpiceModelNote]:
        """Return SPICE-model reliability notes, optionally filtered.

        ``part`` matches case-insensitively as a substring (so ``"IRF"`` finds
        ``IRF740``); ``status`` filters on ``ok`` | ``conditional`` | ``avoid``.
        """
        out = self._spice_notes
        if part is not None:
            p = part.lower()
            out = [n for n in out if p in n.part.lower()]
        if status is not None:
            out = [n for n in out if n.status == status]
        return list(out)

    # ---- component failure modes (optional, unused by the facade) ----------

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
