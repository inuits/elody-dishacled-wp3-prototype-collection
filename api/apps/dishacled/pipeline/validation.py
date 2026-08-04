"""Static validation of a pipeline chain.

Walk the connections a pipeline declares (see `connections.py`) and decide, for
each producer->consumer link, whether the producer's output shape is acceptable
to the consumer's input shape.

Compatibility is *not* decided by comparing the two shapes structurally.
Subsumption over SHACL is undecidable in general and brittle in practice, so
the demonstrator's own hint is followed instead: **produce a sample under the
output shape and SHACL-validate it against the input shape**. The producer says
"this is the kind of thing I emit"; the sample makes that concrete; the
consumer's shape then judges it with an ordinary validation run. A unit
mismatch therefore surfaces as `sh:hasValue` failing on `demo:unit` with the
actual value `"mm"` in hand -- located and quotable -- rather than as a bare
"the two IRIs differ".

The result is a structured violation list, `{from, to, constraint, expected,
actual, message}` per the brief, plus a per-connection verdict that is stamped
back onto the pipeline's `hasProcessor` relations so the UI can render it.

Nothing here refuses to *model* a broken chain -- `connections.py` deliberately
lets one be composed. Blocking happens at export time
(`resources/pipeline_export.py`), which is the point where a broken pipeline
would otherwise leave the system.
"""

from dataclasses import dataclass, field, replace

from pyshacl import validate as shacl_validate
from rdflib import BNode, Graph, Literal, Namespace, RDF, URIRef
from rdflib.collection import Collection as RdfCollection

from apps.dishacled.pipeline.connections import (
    CONNECTIONS_KEY,
    STATE_FIELD,
    STATE_INVALID,
    STATE_MESSAGE_FIELD,
    STATE_UNKNOWN,
    STATE_VALID,
    connections_for_pipeline,
)


SH = Namespace("http://www.w3.org/ns/shacl#")
XSD = Namespace("http://www.w3.org/2001/XMLSchema#")

# Where the synthesised sample instance lives. It never leaves this module, but
# it needs *some* IRI for the validation report to point at.
SAMPLE = Namespace("urn:dishacled:sample:")
SAMPLE_NODE = SAMPLE["instance"]

SEVERITY_VIOLATION = "violation"
SEVERITY_WARNING = "warning"

MISSING_SHAPE = "missingShape"

# Constraints that all say the same thing about one property -- "the value the
# producer emits is not one this consumer accepts". A shape that fixes a value
# usually spells it out more than once (`sh:hasValue` and `sh:in` together, as
# the demo shapes do), and reporting each spelling separately would list one
# problem several times. The most specific spelling wins.
VALUE_CONSTRAINTS = ("hasValue", "in", "pattern", "datatype")

# One representative value per datatype, used when a property fixes no concrete
# value of its own. The point is only to be well-typed: a consumer that
# constrains the datatype must accept it, and one that constrains the value
# must reject it for the right reason.
_SAMPLE_LITERALS = {
    XSD.string: "sample",
    XSD.decimal: "1.0",
    XSD.double: "1.0",
    XSD.float: "1.0",
    XSD.integer: "1",
    XSD.int: "1",
    XSD.long: "1",
    XSD.boolean: "true",
    XSD.date: "2024-01-01",
    XSD.dateTime: "2024-01-01T00:00:00Z",
    XSD.time: "00:00:00",
    XSD.anyURI: "urn:dishacled:sample",
}


def _local_name(term) -> str:
    text = str(term)
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rsplit("/", 1)[-1]


def _shape_ttl(payload) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("ttl") or "")


def _shape_iri(payload):
    if not isinstance(payload, dict):
        return None
    return payload.get("iri")


def _shape_label(payload) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("label") or "") or _local_name(payload.get("iri") or "")


def _node_shape(graph: Graph, shape_iri):
    """The node shape a payload is about.

    A shape sub-graph extracted from the catalog is usually a single shape, but
    a referenced shape (`sh:node`) can be dragged in alongside it, so the IRI
    the payload names wins when it is present.
    """
    if shape_iri:
        candidate = URIRef(str(shape_iri))
        if (candidate, None, None) in graph:
            return candidate
    shapes = [
        s
        for s in graph.subjects(RDF.type, SH.NodeShape)
        if (s, SH.property, None) in graph
    ]
    if shapes:
        return shapes[0]
    return next(graph.subjects(RDF.type, SH.NodeShape), None)


def _in_values(graph: Graph, node) -> list:
    listed = graph.value(node, SH["in"])
    if listed is None:
        return []
    try:
        return list(RdfCollection(graph, listed))
    except Exception:
        return []


def _sample_value(graph: Graph, property_shape):
    """The one value a sample carries for a property.

    Order matters: a fixed value describes the producer most precisely, an
    enumeration next, and a bare datatype least.
    """
    fixed = graph.value(property_shape, SH.hasValue)
    if fixed is not None:
        return fixed

    allowed = _in_values(graph, property_shape)
    if allowed:
        return allowed[0]

    datatype = graph.value(property_shape, SH.datatype)
    if datatype is not None:
        return Literal(
            _SAMPLE_LITERALS.get(datatype, "sample"), datatype=datatype
        )

    class_ref = graph.value(property_shape, SH["class"])
    if class_ref is not None:
        return ("node", class_ref)

    if graph.value(property_shape, SH.nodeKind) == SH.IRI:
        return URIRef("urn:dishacled:sample:value")

    return Literal("sample")


def sample_for_shape(shape_ttl: str, shape_iri=None):
    """A data graph holding one instance produced under `shape_ttl`.

    Returns `(graph, node)`, or None when the shape cannot be read. Only
    single-predicate paths are sampled; a property path expression describes a
    traversal rather than a field, and inventing data for one would say more
    than the producer actually claims.
    """
    graph = Graph()
    try:
        graph.parse(data=shape_ttl or "", format="turtle")
    except Exception:
        return None

    shape = _node_shape(graph, shape_iri)
    if shape is None:
        return None

    data = Graph()
    for prefix, namespace in graph.namespaces():
        data.bind(prefix, namespace)

    for target_class in graph.objects(shape, SH.targetClass):
        data.add((SAMPLE_NODE, RDF.type, target_class))

    for property_shape in graph.objects(shape, SH.property):
        path = graph.value(property_shape, SH.path)
        if not isinstance(path, URIRef):
            continue
        max_count = graph.value(property_shape, SH.maxCount)
        if max_count is not None and int(max_count) < 1:
            continue

        value = _sample_value(graph, property_shape)
        if isinstance(value, tuple):
            node = BNode()
            data.add((node, RDF.type, value[1]))
            value = node
        data.add((SAMPLE_NODE, path, value))

    return data, SAMPLE_NODE


@dataclass(frozen=True)
class Violation:
    """One reason a connection is not acceptable.

    `source`/`target` are port references (`<componentId>|<port>`), so a
    violation always points at the exact link it came from rather than at a
    component in general.
    """

    source: str
    target: str
    constraint: str
    expected: object
    actual: object
    message: str
    source_label: str = ""
    target_label: str = ""
    path: str | None = None
    severity: str = SEVERITY_VIOLATION

    @property
    def blocks_export(self) -> bool:
        return self.severity == SEVERITY_VIOLATION

    def to_dict(self) -> dict:
        return {
            "from": self.source,
            "to": self.target,
            "constraint": self.constraint,
            "expected": self.expected,
            "actual": self.actual,
            "message": self.message,
            "fromLabel": self.source_label,
            "toLabel": self.target_label,
            "path": self.path,
            "severity": self.severity,
        }


def _expected_for(graph: Graph, property_shape, constraint: str):
    """What the consumer's property shape asks for, in readable form."""
    if property_shape is None:
        return None

    fixed = graph.value(property_shape, SH.hasValue)
    allowed = _in_values(graph, property_shape)

    if constraint == "hasValue" and fixed is not None:
        return str(fixed)
    if constraint == "in" and allowed:
        return [str(v) for v in allowed]
    if constraint == "minCount":
        value = graph.value(property_shape, SH.minCount)
        return f"at least {value}" if value is not None else None
    if constraint == "maxCount":
        value = graph.value(property_shape, SH.maxCount)
        return f"at most {value}" if value is not None else None
    if constraint == "datatype":
        value = graph.value(property_shape, SH.datatype)
        return _local_name(value) if value is not None else None
    if constraint in ("class", "nodeKind"):
        value = graph.value(property_shape, SH[constraint])
        return _local_name(value) if value is not None else None
    if constraint == "pattern":
        value = graph.value(property_shape, SH.pattern)
        return str(value) if value is not None else None

    # fall back to whatever the shape does fix, so a constraint this function
    # does not know by name still reports something useful
    if fixed is not None:
        return str(fixed)
    if allowed:
        return [str(v) for v in allowed]
    return None


def _constraint_rank(report_graph: Graph, result) -> int:
    """Order results so the most specific spelling of a value constraint wins."""
    component = report_graph.value(result, SH.sourceConstraintComponent)
    constraint = _local_name(component).removesuffix("ConstraintComponent")
    constraint = constraint[:1].lower() + constraint[1:]
    if constraint in VALUE_CONSTRAINTS:
        return VALUE_CONSTRAINTS.index(constraint)
    return len(VALUE_CONSTRAINTS)


def _reason(constraint: str, expected, actual) -> str:
    if constraint == "minCount":
        return "is required but the producer emits nothing for it"
    if constraint == "maxCount":
        return f"may occur {expected}, but the producer emits more"
    if expected is None:
        return f"does not satisfy the consumer's {constraint} constraint"
    if actual in (None, ""):
        return f"expected {expected!r}, but the producer emits nothing for it"
    return f"expected {expected!r}, but the producer emits {actual!r}"


def _missing_shape_violation(source, target, source_label, target_label, side):
    subject = source_label or source
    if side == "output":
        message = (
            f"{subject} declares no output shape, so what it feeds "
            f"{target_label or target} cannot be checked."
        )
        expected = "an output shape on the producer"
    else:
        message = (
            f"{target_label or target} declares no input shape, so what "
            f"{subject} feeds it cannot be checked."
        )
        expected = "an input shape on the consumer"
    return Violation(
        source=source,
        target=target,
        constraint=MISSING_SHAPE,
        expected=expected,
        actual=None,
        message=message,
        source_label=source_label,
        target_label=target_label,
        severity=SEVERITY_WARNING,
    )


def validate_shape_pair(
    output_shape,
    input_shape,
    source: str = "producer",
    target: str = "consumer",
    source_label: str = "",
    target_label: str = "",
) -> list[Violation]:
    """Is data produced under `output_shape` acceptable to `input_shape`?

    A sample is produced under the output shape and validated against the input
    shape, pinned with `sh:targetNode` so it is judged whatever class it
    happens to carry -- the question asked is "must the consumer accept this?",
    not "does this happen to fall inside the consumer's targets?".
    """
    output_ttl = _shape_ttl(output_shape)
    input_ttl = _shape_ttl(input_shape)
    if not output_ttl:
        return [
            _missing_shape_violation(
                source, target, source_label, target_label, "output"
            )
        ]
    if not input_ttl:
        return [
            _missing_shape_violation(
                source, target, source_label, target_label, "input"
            )
        ]

    sample = sample_for_shape(output_ttl, _shape_iri(output_shape))
    if sample is None:
        return [
            _missing_shape_violation(
                source, target, source_label, target_label, "output"
            )
        ]
    data_graph, sample_node = sample

    shapes_graph = Graph()
    try:
        shapes_graph.parse(data=input_ttl, format="turtle")
    except Exception:
        return [
            _missing_shape_violation(
                source, target, source_label, target_label, "input"
            )
        ]

    shape = _node_shape(shapes_graph, _shape_iri(input_shape))
    if shape is None:
        return [
            _missing_shape_violation(
                source, target, source_label, target_label, "input"
            )
        ]
    # Judge exactly this node, and only once: leaving sh:targetClass in place
    # alongside sh:targetNode would report every failure twice whenever the
    # sample happens to carry the consumer's target class.
    shapes_graph.remove((shape, SH.targetClass, None))
    shapes_graph.add((shape, SH.targetNode, sample_node))

    try:
        _, report_graph, _ = shacl_validate(
            data_graph,
            shacl_graph=shapes_graph,
            ont_graph=None,
            inference="none",
            advanced=True,
            meta_shacl=False,
            debug=False,
        )
    except Exception as error:  # pragma: no cover - defensive
        return [
            Violation(
                source=source,
                target=target,
                constraint="validationError",
                expected=None,
                actual=None,
                message=(
                    f"The connection from {source_label or source} to "
                    f"{target_label or target} could not be validated: {error}"
                ),
                source_label=source_label,
                target_label=target_label,
                severity=SEVERITY_WARNING,
            )
        ]

    violations = []
    seen = set()
    covered_paths = set()
    results = sorted(
        report_graph.subjects(RDF.type, SH.ValidationResult),
        key=lambda r: _constraint_rank(report_graph, r),
    )
    for result in results:
        component = report_graph.value(result, SH.sourceConstraintComponent)
        constraint = _local_name(component).removesuffix("ConstraintComponent")
        constraint = constraint[:1].lower() + constraint[1:]

        property_shape = report_graph.value(result, SH.sourceShape)
        result_path = report_graph.value(result, SH.resultPath)
        name = shapes_graph.value(property_shape, SH.name) if property_shape else None
        path = str(name) if name else (_local_name(result_path) if result_path else None)

        actual_term = report_graph.value(result, SH.value)
        if actual_term is None and isinstance(result_path, URIRef):
            # sh:hasValue fails on the focus node, not on a value, so the
            # report carries no sh:value. The sample is right there, so read
            # what the producer actually emits rather than reporting nothing.
            actual_term = data_graph.value(sample_node, result_path)
        actual = str(actual_term) if actual_term is not None else None
        expected = _expected_for(shapes_graph, property_shape, constraint)

        if constraint in VALUE_CONSTRAINTS:
            if path in covered_paths:
                continue
            covered_paths.add(path)

        key = (constraint, path, actual)
        if key in seen:
            continue
        seen.add(key)

        subject = source_label or source
        object_ = target_label or target
        located = f'"{path}"' if path else "the payload"
        violations.append(
            Violation(
                source=source,
                target=target,
                constraint=constraint,
                expected=expected,
                actual=actual,
                message=(
                    f"{subject} → {object_}: {located} "
                    f"{_reason(constraint, expected, actual)}."
                ),
                source_label=source_label,
                target_label=target_label,
                path=path,
            )
        )

    violations.sort(key=lambda v: (v.path or "", v.constraint))
    return violations


def _shape_payload(component, key):
    return ((component or {}).get("data") or {}).get(key)


def validate_connection(connection, components: dict) -> list[Violation]:
    """The violations of one producer->consumer link."""
    return validate_shape_pair(
        _shape_payload(components.get(connection.source), "outputShape"),
        _shape_payload(components.get(connection.target), "inputShape"),
        source=f"{connection.source}|{connection.source_port}",
        target=f"{connection.target}|{connection.target_port}",
        source_label=connection.source_label,
        target_label=connection.target_label,
    )


@dataclass(frozen=True)
class PipelineValidationReport:
    connections: list = field(default_factory=list)
    violations: list = field(default_factory=list)

    @property
    def blocking(self) -> list:
        return [v for v in self.violations if v.blocks_export]

    @property
    def warnings(self) -> list:
        return [v for v in self.violations if not v.blocks_export]

    @property
    def is_valid(self) -> bool:
        return not self.blocking

    @property
    def summary(self) -> str:
        if not self.connections:
            return "This pipeline declares no connections."
        if self.is_valid and not self.warnings:
            count = len(self.connections)
            if count == 1:
                return "The single connection is compatible."
            return f"All {count} connections are compatible."
        parts = []
        broken = {c.id for c in self.connections if c.state == STATE_INVALID}
        unknown = {c.id for c in self.connections if c.state == STATE_UNKNOWN}
        if broken:
            count = len(broken)
            parts.append(f"{count} incompatible connection{'' if count == 1 else 's'}")
        if unknown:
            count = len(unknown)
            parts.append(f"{count} unverifiable connection{'' if count == 1 else 's'}")
        return f"{', '.join(parts)}." if parts else "All connections are compatible."

    def to_dict(self) -> dict:
        return {
            "isValid": self.is_valid,
            "summary": self.summary,
            "violations": [v.to_dict() for v in self.violations],
            "connections": [c.to_dict() for c in self.connections],
        }

    def as_turtle_comments(self) -> str:
        """The report as a comment block, for an export that was forced anyway.

        A forced export has to carry its own warning: the file outlives the
        request that produced it, and whoever runs it never saw the refusal.
        """
        lines = [
            "# WARNING: this pipeline was exported despite failing validation.",
            f"# {self.summary}",
        ]
        for violation in self.violations:
            lines.append(f"#   - {violation.message}")
        return "\n".join(lines) + "\n\n"


def _verdict(violations) -> tuple[str, str]:
    """The state and message one connection's violations amount to."""
    blocking = [v for v in violations if v.blocks_export]
    if blocking:
        return STATE_INVALID, " ".join(v.message for v in blocking)
    warnings = [v for v in violations if not v.blocks_export]
    if warnings:
        return STATE_UNKNOWN, " ".join(v.message for v in warnings)
    return STATE_VALID, ""


def validate_pipeline(pipeline, components: dict) -> PipelineValidationReport:
    """Validate every connection a pipeline declares.

    A dataset feeding the first processor is an ordinary connection here -- the
    contract model gives a dataset an output port like any other producer -- so
    "source shape vs first input" needs no special case.
    """
    connections = []
    violations = []
    for connection in connections_for_pipeline(pipeline, components):
        found = validate_connection(connection, components)
        state, message = _verdict(found)
        violations.extend(found)
        connections.append(replace(connection, state=state, state_message=message))
    return PipelineValidationReport(connections=connections, violations=violations)


def _state_keys(port: str) -> tuple[str, str]:
    return (
        f"{CONNECTIONS_KEY}.{port}.{STATE_FIELD}",
        f"{CONNECTIONS_KEY}.{port}.{STATE_MESSAGE_FIELD}",
    )


def apply_validation_state(pipeline, components: dict):
    """Stamp each connection's verdict onto the relation it is stored on.

    The verdict lives beside the connection itself (`connections.<port>.state`)
    rather than in a report on the side, which is what makes it visible without
    any new UI: the processor list already renders a hasProcessor relation's
    metadata, so a mismatch shows up next to the producer that caused it.

    Every state key is cleared first, so a connection that was rewired or
    removed cannot leave a verdict behind that no longer refers to anything.
    Returns a new document; the input is not mutated.
    """
    if not pipeline:
        return pipeline

    report = validate_pipeline(pipeline, components)
    verdicts = {}
    for connection in report.connections:
        verdicts.setdefault(connection.target, {})[connection.target_port] = (
            connection.state,
            connection.state_message,
        )

    relations = []
    for relation in pipeline.get("relations", []) or []:
        metadata = [dict(item) for item in relation.get("metadata", []) or []]
        stale = {
            key
            for item in metadata
            for key in [str(item.get("key") or "")]
            if key.startswith(f"{CONNECTIONS_KEY}.")
            and key.rsplit(".", 1)[-1] in (STATE_FIELD, STATE_MESSAGE_FIELD)
        }
        metadata = [m for m in metadata if str(m.get("key") or "") not in stale]
        for port, (state, message) in verdicts.get(relation.get("key"), {}).items():
            state_key, message_key = _state_keys(port)
            metadata.append({"key": state_key, "value": state})
            metadata.append({"key": message_key, "value": message})
        relations.append({**relation, "metadata": metadata})

    return {**pipeline, "relations": relations}
