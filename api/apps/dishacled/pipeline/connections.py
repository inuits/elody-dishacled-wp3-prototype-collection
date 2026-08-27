"""Directed, typed producer->consumer links between pipeline components.

A component's contract (see `shacl/contracts.py`) says which shape it consumes
and which it produces. This module turns that into something a user can wire
up: every channel-typed config property is a **port**, and a **connection**
says "component A's output port feeds component B's input port, over channel
C".

Connections are authored from the consumer side. Each input port of a
component gets one form field on the pipeline's `hasProcessor` relation:

    connections.<inputPort>.from     -> "<componentId>|<outputPort>"
    connections.<inputPort>.channel  -> optional channel name
    connections.<inputPort>.state    -> filled in by validation (B3)

Storing them as relation metadata means the link lives on the pipeline entity
and rides the existing relation-config save path -- no new write API, and the
same dotted-key convention the SHACL config fields already use. Reading them
back yields canonical `Connection` objects, which is what the TTL export and
the chain validation both consume.

Nothing here rejects a mismatched pair: composing an mm producer into a cm
consumer must be possible, otherwise the mismatch B3 exists to report could
never be demonstrated. Compatibility is reported (`is_shape_match`), not
enforced.
"""

import re
from dataclasses import dataclass


# Config properties of these SHACL classes are the wiring points. They mirror
# the RDF-Connect channel vocabulary the processors already use, which is what
# "over the existing channel concept" means in practice.
READER_CLASS = "rdfc:Reader"
WRITER_CLASS = "rdfc:Writer"
CHANNEL_CLASS = "rdfc:Channel"

DIRECTION_IN = "in"
DIRECTION_OUT = "out"
ROLE_FOR_DIRECTION = {DIRECTION_IN: "input", DIRECTION_OUT: "output"}
SHAPE_KEY_FOR_DIRECTION = {DIRECTION_IN: "inputShape", DIRECTION_OUT: "outputShape"}

PROCESSOR_RELATION = "hasProcessor"

# Relation-metadata layout. Kept in one place so the form builder, the reader
# and the export cannot drift apart.
CONNECTIONS_KEY = "connections"
SOURCE_FIELD = "from"
CHANNEL_FIELD = "channel"
STATE_FIELD = "state"
STATE_MESSAGE_FIELD = "stateMessage"

# Separates the component id from the port name in a stored reference. Not "-"
# or ":" -- component ids contain both (`local--http-poller-cm`).
PORT_SEPARATOR = "|"

# Per-connection validation state. `unvalidated` is the placeholder a
# connection carries until it has been checked; the other three are the
# verdicts `pipeline/validation.py` writes.
STATE_UNVALIDATED = "unvalidated"
STATE_VALID = "valid"
STATE_INVALID = "invalid"
# one of the two endpoints declares no shape, so nothing can be concluded
STATE_UNKNOWN = "unknown"

# A dataset declares an output shape but has no config shape, so it has no
# writer property to derive a port from. It still needs one to be connectable.
DATASET_OUTPUT_PORT = "output"


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")


def nest_metadata(metadata) -> dict:
    """Flat dotted-key metadata -> nested dict.

    [{"key": "connections.input.from", "value": "a|out"}]
        -> {"connections": {"input": {"from": "a|out"}}}
    """
    result: dict = {}
    for item in metadata or []:
        key = item.get("key")
        if not key:
            continue
        parts = str(key).split(".")
        cursor = result
        for part in parts[:-1]:
            nxt = cursor.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[part] = nxt
            cursor = nxt
        cursor[parts[-1]] = item.get("value")
    return result


def component_name(document) -> str:
    for item in (document or {}).get("metadata", []) or []:
        if item.get("key") == "name" and item.get("value"):
            return str(item["value"])
    return str((document or {}).get("_id") or "")


def _component_id(document) -> str:
    identifiers = (document or {}).get("identifiers") or []
    return str((document or {}).get("_id") or (identifiers[0] if identifiers else ""))


def _shape_match(source_shape, target_shape):
    """True/False when both shapes are known, None when either is not.

    An untyped processor (no contract entry) must not be reported as
    incompatible -- nothing is known about it either way.
    """
    if not source_shape or not target_shape:
        return None
    return source_shape == target_shape


@dataclass(frozen=True)
class Port:
    """One wiring point on a component."""

    component: str
    name: str
    direction: str  # "in" | "out"
    role: str  # "input" | "output"
    label: str
    shape_iri: str | None = None
    shape_label: str | None = None
    is_required: bool = False

    @property
    def reference(self) -> str:
        """The value stored when this port is picked as a producer."""
        return f"{self.component}{PORT_SEPARATOR}{self.name}"

    def to_dict(self) -> dict:
        return {
            "component": self.component,
            "name": self.name,
            "direction": self.direction,
            "role": self.role,
            "label": self.label,
            "shapeIri": self.shape_iri,
            "shapeLabel": self.shape_label,
            "isRequired": self.is_required,
            "reference": self.reference,
        }


def parse_port_reference(value) -> tuple[str | None, str | None]:
    if not value or PORT_SEPARATOR not in str(value):
        return None, None
    component, _, port = str(value).partition(PORT_SEPARATOR)
    if not component or not port:
        return None, None
    return component, port


def _directions_for(class_ref) -> tuple[str, ...]:
    if class_ref == READER_CLASS:
        return (DIRECTION_IN,)
    if class_ref == WRITER_CLASS:
        return (DIRECTION_OUT,)
    if class_ref == CHANNEL_CLASS:
        # a bare channel is readable and writable; offer it both ways
        return (DIRECTION_IN, DIRECTION_OUT)
    return ()


def ports_for_component(document) -> list[Port]:
    """The ports of a component, from its config properties and its contract.

    Config properties supply the port names (they are what the pipeline TTL
    actually binds); the contract supplies the shape each port carries. A
    component without a contract still has ports, just untyped ones.
    """
    data = (document or {}).get("data") or {}
    component = _component_id(document)
    if not component:
        return []

    shapes = {
        direction: data.get(key) or {}
        for direction, key in SHAPE_KEY_FOR_DIRECTION.items()
    }

    ports: list[Port] = []
    seen: set[tuple[str, str]] = set()
    for prop in data.get("properties") or []:
        name = prop.get("name")
        if not name:
            continue
        for direction in _directions_for(prop.get("classRef")):
            if (name, direction) in seen:
                continue
            seen.add((name, direction))
            shape = shapes[direction]
            ports.append(
                Port(
                    component=component,
                    name=str(name),
                    direction=direction,
                    role=ROLE_FOR_DIRECTION[direction],
                    label=str(name),
                    shape_iri=shape.get("iri"),
                    shape_label=shape.get("label"),
                    is_required=bool(prop.get("isRequired")),
                )
            )

    if not ports and data.get("componentKind") == "dataset" and shapes[DIRECTION_OUT]:
        # a dataset is pure source: it produces its output shape and consumes
        # nothing, so it gets exactly one synthetic output port
        ports.append(
            Port(
                component=component,
                name=DATASET_OUTPUT_PORT,
                direction=DIRECTION_OUT,
                role=ROLE_FOR_DIRECTION[DIRECTION_OUT],
                label=DATASET_OUTPUT_PORT,
                shape_iri=shapes[DIRECTION_OUT].get("iri"),
                shape_label=shapes[DIRECTION_OUT].get("label"),
            )
        )

    return ports


def input_ports(ports) -> list[Port]:
    return [port for port in ports if port.direction == DIRECTION_IN]


def output_ports(ports) -> list[Port]:
    return [port for port in ports if port.direction == DIRECTION_OUT]


@dataclass(frozen=True)
class Connection:
    """A directed link from one step's producer port to another's consumer port.

    `source` and `target` are *step* ids: which two steps are wired. The
    component behind each is carried alongside, because what a link may carry
    is a property of the component (its input and output shapes), not of the
    step -- two steps of one component are checked against the same shapes.
    """

    source: str
    source_port: str
    target: str
    target_port: str
    channel: str
    # the components behind the two steps; default to the step ids so a
    # Connection built by hand still resolves
    source_key: str = ""
    target_key: str = ""
    source_role: str = "output"
    target_role: str = "input"
    source_shape: str | None = None
    target_shape: str | None = None
    source_label: str = ""
    target_label: str = ""
    state: str = STATE_UNVALIDATED
    state_message: str = ""

    @property
    def id(self) -> str:
        return (
            f"{self.source}{PORT_SEPARATOR}{self.source_port}"
            f"->{self.target}{PORT_SEPARATOR}{self.target_port}"
        )

    @property
    def is_shape_match(self):
        return _shape_match(self.source_shape, self.target_shape)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "sourceKey": self.source_key or self.source,
            "targetKey": self.target_key or self.target,
            "sourcePort": self.source_port,
            "sourceRole": self.source_role,
            "sourceShape": self.source_shape,
            "sourceLabel": self.source_label,
            "target": self.target,
            "targetPort": self.target_port,
            "targetRole": self.target_role,
            "targetShape": self.target_shape,
            "targetLabel": self.target_label,
            "channel": self.channel,
            "isShapeMatch": self.is_shape_match,
            "state": self.state,
            "stateMessage": self.state_message,
        }


def connection_metadata(
    input_port: str, source_reference: str, channel: str | None = None
) -> list[dict]:
    """The relation metadata a connection is stored as.

    Mirrors what the connect form saves, so tests and any programmatic caller
    write exactly the same keys the UI does.
    """
    metadata = [
        {
            "key": f"{CONNECTIONS_KEY}.{input_port}.{SOURCE_FIELD}",
            "value": source_reference,
        }
    ]
    if channel:
        metadata.append(
            {
                "key": f"{CONNECTIONS_KEY}.{input_port}.{CHANNEL_FIELD}",
                "value": channel,
            }
        )
    return metadata


def channel_name_between(
    source: str, source_port: str, target: str, target_port: str
) -> str:
    """The channel a connection gets when the user did not name one.

    Built from the two *step* ids, not the two component names: a component
    used twice feeds two different steps, and naming both channels after the
    component would collapse them back into one.
    """
    return "-".join(
        [slugify(source), slugify(source_port), "to", slugify(target), slugify(target_port)]
    )


def default_channel_name(
    source_document, source_port: str, target_document, target_port: str
) -> str:
    """The same, addressed by document -- what a single-instance pipeline gets."""
    return channel_name_between(
        component_name(source_document),
        source_port,
        component_name(target_document),
        target_port,
    )


# A step's own identity within its pipeline. It is the slug the step's IRI ends
# in, so a pipeline read back out of the store carries it without the definition
# needing a new triple for it.
#
# It lives in the *relation key*, because that is what the framework addresses a
# related entity by: the processor list fetches one row per key, and a config
# modal writes back to the relation whose key matches the row it was opened on
# (`useFormHelper.parseRelationMetadataForFormSubmit`). A step the UI can show
# and configure on its own therefore has to be a key of its own.
#
#     rdf-connect--log-processor-ts--LogProcessorJs~logprocessorjs-2
#
# `~` because these ids travel in URL paths (`/processors/<id>/shui.ttl`) and in
# filter values, and it is unreserved in both. It is also kept as relation
# metadata, which is what an id typed by hand or set before a save looks like.
INSTANCE_FIELD = "instance"
INSTANCE_SEPARATOR = "~"


def split_component_key(key) -> tuple[str, str | None]:
    """`component~step` -> (component, step); a bare key names no step."""
    text = str(key or "")
    component, separator, instance = text.partition(INSTANCE_SEPARATOR)
    if not separator or not instance:
        return text, None
    return component, instance


@dataclass(frozen=True)
class Instance:
    """One step: a component, used once, with its own configuration.

    A pipeline is instances of components rather than components -- the
    tutorial's runs two `rdfc:LogProcessorJs`, and the toolchain's own
    reference definition has two `LogProcessorJs` steps and two `Sdsify` steps.
    `tcs:InstancePipelineComponent` is a `p-plan:Step`, and steps are the many
    side of `prov:specializationOf`, so this is the modelling the vocabulary
    already assumes.
    """

    id: str
    key: str  # the relation key: the component, or `component~step`
    label: str
    relation: dict

    @property
    def component_id(self) -> str:
        """The component this step is one of."""
        return split_component_key(self.key)[0]

    @property
    def metadata(self) -> list:
        return self.relation.get("metadata", []) or []


def instance_id_of(relation) -> str | None:
    """The identity a relation already carries, if any."""
    for item in relation.get("metadata", []) or []:
        if item.get("key") == INSTANCE_FIELD and item.get("value"):
            return str(item["value"])
    return None


def instances_of(pipeline, components: dict | None = None) -> list[Instance]:
    """Every step of a pipeline, in relation order, each with an id of its own.

    The id is the relation's stored `instance` when it has one -- which is how
    a saved connection keeps pointing at the same step -- and otherwise the
    component's name, made unique within the pipeline. So a pipeline with one
    logger still calls it `logprocessorjs`, and a second one is
    `logprocessorjs-2` rather than the same step twice.
    """
    components = components or {}
    instances: list[Instance] = []
    taken: set[str] = set()

    for relation in (pipeline or {}).get("relations", []) or []:
        if relation.get("type") != PROCESSOR_RELATION:
            continue
        key = relation.get("key")
        if not key:
            continue

        document = components.get(key) or {}
        component_id, from_key = split_component_key(key)
        label = component_name(document) if document else component_id
        # the key is the authority: it is what the UI addressed this step by
        stored = from_key or instance_id_of(relation)
        instance = stored or _unique(slugify(label) or slugify(component_id), taken)
        if stored:
            taken.add(stored)
        instances.append(
            Instance(id=instance, key=key, label=label, relation=relation)
        )
    return instances


def _unique(candidate: str, taken: set) -> str:
    if candidate not in taken:
        taken.add(candidate)
        return candidate
    index = 2
    while f"{candidate}-{index}" in taken:
        index += 1
    unique = f"{candidate}-{index}"
    taken.add(unique)
    return unique


def processor_keys(pipeline) -> list[str]:
    """The component ids a pipeline holds, in relation order, deduplicated."""
    keys = []
    for relation in (pipeline or {}).get("relations", []) or []:
        if relation.get("type") != PROCESSOR_RELATION:
            continue
        key = relation.get("key")
        if key and key not in keys:
            keys.append(key)
    return keys


def _connection_settings(relation) -> dict:
    """`connections` sub-tree of one hasProcessor relation's metadata."""
    settings = nest_metadata(relation.get("metadata", [])).get(CONNECTIONS_KEY)
    if not isinstance(settings, dict):
        return {}
    normalised = {}
    for port, value in settings.items():
        # tolerate a bare string, which is what a hand-written
        # `connections.<port>` metadata entry would produce
        normalised[port] = value if isinstance(value, dict) else {SOURCE_FIELD: value}
    return normalised


def resolve_instance(reference: str, instances) -> "Instance | None":
    """The step a stored producer reference names.

    An `instance|port` reference names the step outright. A `component|port`
    one is what pipelines saved before steps had identity carry, and it names
    the component's first step -- unambiguous when the component is used once,
    which is the only case that could have been saved back then.
    """
    if not reference:
        return None
    by_id = {instance.id: instance for instance in instances}
    if reference in by_id:
        return by_id[reference]
    return next(
        (instance for instance in instances if instance.key == reference), None
    )


def connections_for_pipeline(pipeline, components: dict) -> list[Connection]:
    """Every connection declared on a pipeline, as directed links.

    `components` maps component id -> component document (the `githubProcessor`
    documents the pipeline's hasProcessor relations point at). Connections are
    between *steps*: a component used twice has two of them, each with its own
    wiring. Anything that cannot be resolved into a real producer port is
    dropped rather than surfaced as a half-connection.
    """
    instances = instances_of(pipeline, components)
    ports_by_component = {
        instance.key: ports_for_component(components.get(instance.key) or {})
        for instance in instances
    }

    connections: list[Connection] = []
    for instance in instances:
        target = instance.id
        target_key = instance.key
        target_ports = {
            p.name: p for p in input_ports(ports_by_component.get(target_key, []))
        }

        for port_name, settings in sorted(
            _connection_settings(instance.relation).items()
        ):
            reference, source_port_name = parse_port_reference(
                settings.get(SOURCE_FIELD)
            )
            source_instance = resolve_instance(reference, instances)
            if source_instance is None or source_instance.id == target:
                continue
            source = source_instance.id
            source_port = next(
                (
                    p
                    for p in output_ports(
                        ports_by_component.get(source_instance.key, [])
                    )
                    if p.name == source_port_name
                ),
                None,
            )
            if source_port is None:
                continue
            target_port = target_ports.get(port_name)
            if target_port is None and target_ports:
                # the component has ports but not this one -- stale metadata
                continue

            source_document = components.get(source_instance.key) or {}
            target_document = components.get(target_key) or {}
            connections.append(
                Connection(
                    source=source,
                    source_port=source_port.name,
                    target=target,
                    target_port=port_name,
                    source_key=source_instance.key,
                    target_key=target_key,
                    channel=settings.get(CHANNEL_FIELD)
                    or channel_name_between(
                        source, source_port.name, target, port_name
                    ),
                    source_shape=source_port.shape_iri,
                    target_shape=target_port.shape_iri if target_port else None,
                    source_label=component_name(source_document),
                    target_label=component_name(target_document),
                    state=settings.get(STATE_FIELD) or STATE_UNVALIDATED,
                    state_message=settings.get(STATE_MESSAGE_FIELD) or "",
                )
            )
    return connections


def producer_options_for(
    pipeline, components: dict, target_id: str, target_shape: str | None = None
) -> list[dict]:
    """The output ports a given component could be fed from.

    Incompatible producers are included and flagged, never filtered out: the
    user has to be able to compose the mismatching chain the validation step
    then reports on.
    """
    options = []
    for key in processor_keys(pipeline):
        if key == target_id:
            continue
        document = components.get(key) or {}
        for port in output_ports(ports_for_component(document)):
            options.append(
                {
                    "value": port.reference,
                    "label": f"{component_name(document)} → {port.name}",
                    "component": key,
                    "componentLabel": component_name(document),
                    "port": port.name,
                    "shapeIri": port.shape_iri,
                    "shapeLabel": port.shape_label,
                    "isShapeMatch": _shape_match(port.shape_iri, target_shape),
                }
            )
    return options
