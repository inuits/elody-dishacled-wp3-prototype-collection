"""Input/output/config shapes per pipeline component.

A component's config shape says how to *configure* it; its input and output
shapes say what data it *consumes* and *produces*. Those two are what let a
pipeline editor connect one component's output to the next one's input, and
what lets the resulting chain be validated.

Shapes are attached with the DiSHACLed discovery vocabulary
(https://dishacled.github.io/discovery-specification/). The specification
offers three alternatives and tells a client to treat them as a logical OR
with no prioritisation; we read the two that can carry a role:

    2.2  <component> dcat:qualifiedRelation [
             a dcat:Relationship ;
             dcat:hadRole tcs:inputShape ;
             dcterms:relation <shape> ] .

    (the `a dcat:Relationship` type is optional -- the reference catalog at
    thcarsten/toolchain-specification omits it -- and `dcterms:conformsTo` is
    accepted in place of `dcterms:relation`.)

Plain roleless `dcterms:conformsTo` on the component (spec 2.1) is
deliberately *not* read: as the specification itself notes, it cannot say
whether a shape describes the input or the output, which is the whole point
here.

This module composes ShaclParser rather than extending it -- the same relation
form.py has to shui.py. ShaclParser is a stateless SHACL-only parser; contracts
are a different vocabulary at a different granularity (a catalog of components,
with IRI references that need a graph to resolve).
"""

from dataclasses import dataclass
from functools import cached_property, lru_cache
from pathlib import Path

from rdflib import BNode, Graph, Literal, Namespace, RDF, RDFS, URIRef

from apps.dishacled.pipeline.connections import DATASET_OUTPUT_PORT
from apps.dishacled.shacl.parser import ShaclParser, ShaclProperty


SH = Namespace("http://www.w3.org/ns/shacl#")
DCAT = Namespace("http://www.w3.org/ns/dcat#")
DCT = Namespace("http://purl.org/dc/terms/")
TCS = Namespace("https://w3id.org/toolchain#")
RDFC = Namespace("https://w3id.org/rdf-connect#")
OWL = Namespace("http://www.w3.org/2002/07/owl#")
SPDX = Namespace("http://spdx.org/rdf/terms#")


# --------------------------------------------------------------------------
# The single "component = dcat resource" mapping point.
#
# How a component is recognised, which roles exist, how a shape hangs off a
# role, and how a component IRI becomes an Elody id are all defined here and
# nowhere else, so swapping to the real discovery catalog is a one-file change.
# --------------------------------------------------------------------------

QUALIFIED_RELATION = DCAT.qualifiedRelation
ROLE_PREDICATE = DCAT.hadRole

# tried in order; dcterms:relation is what the spec and the reference catalog
# use, dcterms:conformsTo is accepted because it reads naturally in the same
# position and appears in some hand-written catalogs
SHAPE_LINK_PREDICATES = (DCT.relation, DCT.conformsTo)

# Role IRIs. tcs:configShape already exists in the reference catalog; the
# input/output ones follow its namespace and casing. The spec's own example
# names are accepted as aliases -- it does not mandate any particular value.
ROLES = {
    "config": (TCS.configShape,),
    "input": (TCS.inputShape, TCS.InputDataShape),
    "output": (TCS.outputShape, TCS.OutputDataShape),
}

DATASET_TYPES = (DCAT.Dataset,)

LABEL_PREDICATES = (RDFS.label, DCT.title)
# `rdfs:description` is not an RDFS term, but the reference catalog uses it, so
# it is read by IRI (rdflib's RDFS namespace is closed and would reject it).
COMMENT_PREDICATES = (
    RDFS.comment,
    DCT.description,
    URIRef("http://www.w3.org/2000/01/rdf-schema#description"),
)

# Components declared in this catalog have no GitHub repository, so they get an
# id in their own namespace to keep them apart from `owner--repo` ids.
LOCAL_ID_PREFIX = "local--"

DEFAULT_CONTRACTS_PATH = Path(__file__).parent / "catalog" / "contracts.ttl"

# `owl:imports <./node_modules/...>` is deliberately relative: the toolchain
# generator resolves it against the location the pipeline is mounted at inside
# the container, which we cannot know here. rdflib resolves relative IRIs at
# parse time, so the catalog is parsed against a fixed base that can be
# stripped back off again -- keeping the relative form intact end to end.
CATALOG_BASE = "https://dishacled.github.io/catalog/"


def _local_name(uri) -> str:
    uri_str = str(uri)
    if "#" in uri_str:
        return uri_str.split("#")[-1]
    return uri_str.rsplit("/", 1)[-1]


def _kebab(name: str) -> str:
    out = []
    for i, char in enumerate(name):
        if char.isupper() and i > 0 and not name[i - 1].isupper():
            out.append("-")
        out.append(char.lower())
    return "".join(out)


def local_id_for(component_iri: str) -> str:
    """demo:ThresholdMonitorCm -> 'local--threshold-monitor-cm'."""
    return f"{LOCAL_ID_PREFIX}{_kebab(_local_name(component_iri))}"


def component_iri_from_ttl(ttl_string: str) -> str | None:
    """The component IRI a processor's own TTL implements.

    This is the join key between a repository discovered on GitHub and its
    entry in this catalog. Matches any runtime's implementation predicate
    (`rdfc:jsImplementationOf`, `pyImplementationOf`, ...), the same way
    ShaclParser.parse_main_processor_properties picks the main shape.
    """
    try:
        g = Graph()
        g.parse(data=ttl_string, format="turtle")
    except Exception:
        return None

    for s, p, o in g:
        if o == RDFC.Processor and str(p).split("#")[-1].endswith("ImplementationOf"):
            return str(s)
    return None


def _first_value(g: Graph, subject, predicates):
    for predicate in predicates:
        value = g.value(subject, predicate)
        if value is not None:
            return str(value)
    return None


def extract_shape_graph(g: Graph, node) -> Graph:
    """Copy a shape out of the catalog as a self-contained graph.

    Concise-bounded-description style: every triple on `node`, recursing into
    blank-node objects (which is how `sh:property` and `sh:in` RDF lists are
    written) and into named shapes referenced via `sh:node`/`sh:class`, so the
    result validates on its own without the rest of the catalog.
    """
    out = Graph()
    for prefix, namespace in g.namespaces():
        out.bind(prefix, namespace)

    visited = set()

    def walk(subject):
        if subject in visited:
            return
        visited.add(subject)
        for predicate, obj in g.predicate_objects(subject):
            out.add((subject, predicate, obj))
            if isinstance(obj, BNode):
                walk(obj)
            elif isinstance(obj, URIRef) and (obj, RDF.type, SH.NodeShape) in g:
                walk(obj)

    walk(node)
    return out


@dataclass(frozen=True)
class ShapeRef:
    """One shape attached to a component in a given role.

    Carries three views of the same shape because the consumers differ:
    `iri` identifies it (so an output/input pair can be compared cheaply),
    `ttl` is a self-contained sub-graph (so it can be handed to a validator),
    and `properties` is the parsed form the UI already knows how to render.
    """

    role: str
    iri: str | None
    ttl: str
    label: str | None = None

    @cached_property
    def properties(self) -> list[ShaclProperty]:
        return ShaclParser().parse_shape_properties(self.ttl, self.iri)

    def to_dict(self) -> dict:
        return {
            "iri": self.iri,
            "label": self.label,
            "ttl": self.ttl,
            "properties": [
                {
                    "name": prop.name,
                    "inputFieldType": prop.input_field_type,
                    "isRequired": prop.is_required,
                    "inValues": prop.in_values,
                    "classRef": prop.class_ref,
                }
                for prop in self.properties
            ],
        }


@dataclass(frozen=True)
class PackageRef:
    """One installable dependency of a component."""

    name: str
    version: str | None = None
    # IRI of the package manager that supplies it. The toolchain pipeline
    # generator routes on this: `:npm` lands in package.json, `:pip` in
    # pyproject.toml, anything else is dropped from both.
    supplier: str | None = None

    def to_dict(self) -> dict:
        return {"name": self.name, "version": self.version, "supplier": self.supplier}


@dataclass(frozen=True)
class Deployment:
    """Where a component's implementation comes from.

    Shapes say how to configure and connect a component; this says how to
    actually obtain and load it, which is what a build tool needs. Neither
    piece is derivable from the other, and neither is derivable from the
    repository metadata, so both are declared in the catalog.
    """

    imports: tuple[str, ...] = ()
    packages: tuple[PackageRef, ...] = ()

    def to_dict(self) -> dict:
        return {
            "imports": list(self.imports),
            "packages": [package.to_dict() for package in self.packages],
        }


def _relativise(iri) -> str:
    """Undo the parse-time resolution of a relative `owl:imports`."""
    value = str(iri)
    if value.startswith(CATALOG_BASE):
        return "./" + value[len(CATALOG_BASE) :]
    return value


def _deployment_for(g: Graph, subject) -> Deployment:
    imports = tuple(sorted(_relativise(o) for o in g.objects(subject, OWL.imports)))

    packages = []
    for required in g.objects(subject, DCT.requires):
        if (required, RDF.type, SPDX.Package) not in g:
            # a required *component* (a runner, another service); the pipeline
            # definition derives those from the runtime instead
            continue
        name = g.value(required, SPDX.name)
        if name is None:
            continue
        version = g.value(required, SPDX.versionInfo)
        supplier = g.value(required, SPDX.suppliedBy)
        packages.append(
            PackageRef(
                name=str(name),
                version=str(version) if version is not None else None,
                supplier=str(supplier) if supplier is not None else None,
            )
        )
    return Deployment(
        imports=imports, packages=tuple(sorted(packages, key=lambda p: p.name))
    )


@dataclass(frozen=True)
class ComponentContract:
    iri: str
    label: str | None
    comment: str | None
    kind: str  # "component" | "dataset"
    config_shape: ShapeRef | None
    input_shape: ShapeRef | None
    output_shape: ShapeRef | None
    deployment: Deployment = Deployment()

    @property
    def local_id(self) -> str:
        return local_id_for(self.iri)

    def to_data(self) -> dict:
        """The contract half of an entity's `data` document."""
        return {
            "componentIri": self.iri,
            "componentKind": self.kind,
            "configShape": self.config_shape.to_dict() if self.config_shape else None,
            "inputShape": self.input_shape.to_dict() if self.input_shape else None,
            "outputShape": self.output_shape.to_dict() if self.output_shape else None,
            "deployment": self.deployment.to_dict(),
        }

    def to_raw_ttl(self) -> str:
        """A processor.ttl equivalent for a component that has no repository.

        The existing config-form and pipeline-export paths both work from a
        processor's raw TTL, so synthesising one here lets a catalog-declared
        component travel through them unchanged: the implementation triples
        identify it as a processor, and the config shape supplies the form.
        """
        graph = Graph()
        graph.bind("sh", SH)
        graph.bind("rdfc", RDFC)
        graph.bind("rdfs", RDFS)

        subject = URIRef(self.iri)
        if self.kind == "component":
            # a dataset is a source of data, not a processor implementation
            graph.add((subject, RDFC.jsImplementationOf, RDFC.Processor))
        if self.label:
            graph.add((subject, RDFS.label, Literal(self.label)))
        if self.comment:
            graph.add((subject, RDFS.comment, Literal(self.comment)))

        if self.config_shape:
            config = Graph()
            config.parse(data=self.config_shape.ttl, format="turtle")
            # the config shape must target this component for the form builder
            # to recognise it as the main processor shape
            for shape in config.subjects(RDF.type, SH.NodeShape):
                config.remove((shape, SH.targetClass, None))
                config.add((shape, SH.targetClass, subject))
                break
            graph += config
        elif self.output_shape:
            # A dataset has no config shape and therefore no writer property to
            # hang a channel off. Give it the one port its output role implies,
            # so it can be connected to a consumer and exported like any other
            # stage. `DATASET_OUTPUT_PORT` names the same port the connection
            # model synthesises.
            shape = BNode()
            port = BNode()
            graph.add((shape, RDF.type, SH.NodeShape))
            graph.add((shape, SH.targetClass, subject))
            graph.add((shape, SH.property, port))
            graph.add((port, SH.path, RDFC[DATASET_OUTPUT_PORT]))
            graph.add((port, SH.name, Literal(DATASET_OUTPUT_PORT)))
            graph.add((port, SH["class"], RDFC.Writer))
            graph.add((port, SH.minCount, Literal(1)))

        return graph.serialize(format="turtle")


class ContractCatalog:
    """A parsed contract catalog: components keyed by IRI."""

    def __init__(self, graph: Graph):
        self._graph = graph
        self._contracts = {c.iri: c for c in self._discover()}

    @classmethod
    def from_ttl(cls, ttl_string: str) -> "ContractCatalog":
        graph = Graph()
        try:
            graph.parse(data=ttl_string, format="turtle", publicID=CATALOG_BASE)
        except Exception:
            # an unparseable catalog must not take the whole request down;
            # components simply carry no contract
            return cls(Graph())
        return cls(graph)

    @classmethod
    def from_file(cls, path) -> "ContractCatalog":
        path = Path(path)
        if not path.exists():
            return cls(Graph())
        return cls.from_ttl(path.read_text())

    @classmethod
    def default(cls) -> "ContractCatalog":
        return _default_catalog()

    def all(self) -> list[ComponentContract]:
        return sorted(self._contracts.values(), key=lambda c: c.iri)

    def get(self, component_iri: str | None) -> ComponentContract | None:
        if not component_iri:
            return None
        return self._contracts.get(str(component_iri))

    def get_by_local_id(self, local_id: str) -> ComponentContract | None:
        for contract in self._contracts.values():
            if contract.local_id == local_id:
                return contract
        return None

    # -- internals ---------------------------------------------------------

    def _discover(self) -> list[ComponentContract]:
        g = self._graph
        contracts = []
        for subject in set(g.subjects(QUALIFIED_RELATION, None)):
            shapes = {
                role: self._shape_for_role(subject, role) for role in ROLES
            }
            if not any(shapes.values()):
                # a qualifiedRelation carrying no role we understand is not a
                # contract -- do not invent a component for it
                continue
            is_dataset = any(
                (subject, RDF.type, t) in g for t in DATASET_TYPES
            )
            contracts.append(
                ComponentContract(
                    iri=str(subject),
                    label=_first_value(g, subject, LABEL_PREDICATES),
                    comment=_first_value(g, subject, COMMENT_PREDICATES),
                    kind="dataset" if is_dataset else "component",
                    config_shape=shapes["config"],
                    input_shape=shapes["input"],
                    output_shape=shapes["output"],
                    deployment=_deployment_for(g, subject),
                )
            )
        return contracts

    def _shape_for_role(self, subject, role: str) -> ShapeRef | None:
        g = self._graph
        role_iris = ROLES[role]

        for relation in g.objects(subject, QUALIFIED_RELATION):
            if g.value(relation, ROLE_PREDICATE) not in role_iris:
                continue
            shape_node = None
            for predicate in SHAPE_LINK_PREDICATES:
                shape_node = g.value(relation, predicate)
                if shape_node is not None:
                    break
            if shape_node is None:
                continue
            return ShapeRef(
                role=role,
                iri=str(shape_node) if isinstance(shape_node, URIRef) else None,
                ttl=extract_shape_graph(g, shape_node).serialize(format="turtle"),
                label=_first_value(g, shape_node, LABEL_PREDICATES),
            )
        return None


@lru_cache(maxsize=1)
def _default_catalog() -> ContractCatalog:
    return ContractCatalog.from_file(DEFAULT_CONTRACTS_PATH)
