"""Describe an Elody-known component the way a component catalog does.

A pipeline definition only *names* its components; their identity, runner,
package coordinates and shapes live in the catalog (`catalog.ttl` in
thcarsten/toolchain-specification, `demonstrator-catalog-extensions.ttl` next to
it). Elody knows components that catalog does not -- anything discovered on
GitHub under the processor topic, plus the interim contract catalog in
`shacl/catalog/contracts.ttl` -- so those descriptions have to come from
somewhere.

This module is that description, and it has two consumers:

* `pipeline_definition_serializer.py` embeds it in the exported definition as a
  fragment, to be merged with the toolchain catalog;
* `pipeline/catalog.py` publishes it into the central store's catalog graph, so
  a definition can reference a component by IRI alone and every service reads
  one shared catalog.

They emit the same triples on purpose: two spellings of "what Elody knows about
this component" would be two things to keep in step. What the fragment adds is
only the shape sub-graphs' prefixes and the runner declarations -- both are
properties of the document being assembled, not of the component.

The vocabulary is the discovery spec's:

    <component> a tcs:PipelineComponent, dcat:Resource ;
        dct:identifier "<elody id>" ;
        rdfc:jsImplementationOf rdfc:Processor ;    # the join key
        rdfs:label / rdfs:description ;
        dcat:landingPage "<repository url>" ;
        dct:requires rdfc:NodeRunner, [ a spdx:Package ; ... ] ;
        owl:imports <./node_modules/...> ;
        dcat:qualifiedRelation [ a dcat:Relationship ;
            dcat:hadRole tcs:configShape ; dct:relation <shape> ] .

    <catalog> a tcs:Catalog ; dcat:resource <component> .    # membership
"""

import re
from os import getenv

from rdflib import BNode, Graph, Literal, Namespace, RDF, RDFS, URIRef

from apps.dishacled.serializers.pipeline_ttl_serializer import (
    RUNTIME_TO_RUNNER,
    _ShapeIndex,
    _get_metadata_value,
)
from apps.dishacled.shacl.contracts import extract_shape_graph


TCS = Namespace("https://w3id.org/toolchain#")
RDFC = Namespace("https://w3id.org/rdf-connect#")
DCAT = Namespace("http://www.w3.org/ns/dcat#")
DCT = Namespace("http://purl.org/dc/terms/")
OWL = Namespace("http://www.w3.org/2002/07/owl#")
SPDX = Namespace("http://spdx.org/rdf/terms#")
SH = Namespace("http://www.w3.org/ns/shacl#")

# `rdfs:description` is not an RDFS term, but it is what the reference catalog
# uses for a component's prose description, and rdflib's RDFS namespace is
# closed, so it is built by IRI.
RDFS_DESCRIPTION = URIRef("http://www.w3.org/2000/01/rdf-schema#description")

# The `tcs:Catalog` Elody's component descriptions are members of.
#
# The toolchain's application profile carries
# `tcs:SpecializedComponentIsCatalogedShape`: the component a step
# `prov:specializationOf` names has to be a `dcat:resource` of some
# `tcs:Catalog`. A description that says only `a tcs:PipelineComponent` is
# therefore a component belonging to no catalog, and a definition built on it
# violates the profile on every Elody-only step -- so the registration is part
# of the description rather than something a reader is expected to add.
#
# It is *Elody's* catalog, not one of the toolchain's. The ownership rule
# (`pipeline/catalog.py`) is that the toolchain catalog is authoritative for
# what it carries and Elody only fills the gaps; adding resources to a
# `tcs:Catalog` the toolchain owns would leave a consumer unable to tell which
# catalog claims a component, which is the question precedence is settled on.
# Configuration, because the demonstrator may well want one shared catalog
# subject across its services -- point `CATALOG_IRI` at it and the graphs merge
# into that one.
DEFAULT_CATALOG_IRI = "https://elody.eu/catalog#ElodyCatalog"


def catalog_iri() -> URIRef:
    return URIRef(getenv("CATALOG_IRI", "").strip() or DEFAULT_CATALOG_IRI)


# Role IRIs for the shapes attached to a catalog component. `tcs:configShape`
# is the reference catalog's; the other two come from the contract model.
SHAPE_ROLES = {
    "configShape": TCS.configShape,
    "inputShape": TCS.inputShape,
    "outputShape": TCS.outputShape,
}

# The runtime a component's `rdfc:*ImplementationOf` triple is written with.
# The predicate is the join key between a repository and its catalog entry
# (`component_iri_from_ttl`), so the catalog has to carry it -- a consumer
# reading the graph on its own has no processor file to read it off.
RUNTIME_TO_IMPLEMENTATION = {
    "ts": RDFC.jsImplementationOf,
    "js": RDFC.jsImplementationOf,
    "javascript": RDFC.jsImplementationOf,
    "typescript": RDFC.jsImplementationOf,
    "python": RDFC.pyImplementationOf,
    "py": RDFC.pyImplementationOf,
    "java": RDFC.jvmImplementationOf,
    "kotlin": RDFC.jvmImplementationOf,
    "scala": RDFC.jvmImplementationOf,
}


def bind_prefixes(graph: Graph) -> Graph:
    graph.bind("tcs", TCS)
    graph.bind("rdfc", RDFC)
    graph.bind("dcat", DCAT)
    graph.bind("dct", DCT)
    graph.bind("owl", OWL)
    graph.bind("spdx", SPDX)
    graph.bind("sh", SH)
    return graph


# What may follow a prefix: no slash, and not starting with a character a
# CURIE cannot start with. Deliberately stricter than SPARQL's PN_LOCAL.
_COMPACTS_CLEANLY = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")


XSD_IRI = URIRef("http://www.w3.org/2001/XMLSchema#iri")


def normalise_iri_datatypes(graph: Graph) -> Graph:
    """`sh:datatype xsd:iri` -> `sh:nodeKind sh:IRI`, keeping the original.

    `xsd:iri` is not a datatype; it is how the RDF-Connect processors say "an
    IRI", and a value written under it is an IRI node rather than a literal
    (`pipeline_ttl_serializer.XSD_IRI_NOTE`). The toolchain's harvested catalog
    translates it exactly this way -- `sh:nodeKind sh:IRI ;
    tcs:upstreamDatatype xsd:iri` (`data/catalog/catalog-rdfc.ttl`) -- so a
    fragment that restates the raw form contradicts the value the definition
    carries, and the generator's validation report flags one or the other
    whichever way it is written.
    """
    for shape, _, _ in list(graph.triples((None, SH.datatype, XSD_IRI))):
        graph.remove((shape, SH.datatype, XSD_IRI))
        graph.add((shape, SH.nodeKind, SH.IRI))
        graph.add((shape, TCS.upstreamDatatype, XSD_IRI))
    return graph


def bind_used_namespaces(graph: Graph, skip=None) -> Graph:
    """Give every namespace the document actually uses a prefix.

    Not cosmetic. The toolchain pipeline generator compacts an IRI to a CURIE
    and interpolates the result straight into SPARQL
    (`rdfine.GraphReader.select`, via rdflib's `normalizeUri`); an IRI in a
    namespace the document does not bind comes back as a bare
    `https://…#Thing`, unbracketed, and the query it lands in does not parse.
    So a component Elody describes in a namespace of its own -- or one it
    merely references, like the alert store -- has to arrive with a prefix.

    Existing bindings win, and a namespace with no better name gets `nsN`,
    which is what rdflib would have called it anyway.

    `skip` is a prefix of IRIs to leave alone -- the document's own base, whose
    steps and channels read better absolute and are already bound where it
    matters.
    """
    bound = {str(namespace) for _, namespace in graph.namespaces()}
    index = 0
    for term in set(graph.all_nodes()) | set(graph.predicates()):
        if not isinstance(term, URIRef):
            continue
        text = str(term)
        if skip and text.startswith(str(skip)):
            continue
        if "://" not in text and not text.startswith("urn:"):
            # A relative `owl:imports` (`./node_modules/...`) is deliberately
            # relative -- the runner resolves it against wherever it mounts the
            # pipeline -- and prefixing it would rewrite it into something that
            # resolves somewhere else. Nothing to bind for it either: it is not
            # in a namespace, it is a path.
            continue
        cut = max(text.rfind("#"), text.rfind("/"))
        if cut < 0:
            continue
        namespace, local = text[: cut + 1], text[cut + 1 :]
        if namespace in bound or not _COMPACTS_CLEANLY.match(local):
            # A prefix only helps if the rest is a legal CURIE local name. The
            # generator interpolates the compacted form into SPARQL, so
            # `ns1:some/path` -- syntactically a CURIE with a slash in it --
            # would be worse than leaving the IRI absolute.
            continue
        while True:
            index += 1
            prefix = f"ns{index}"
            if prefix not in dict(graph.namespaces()):
                break
        graph.bind(prefix, Namespace(namespace))
        bound.add(namespace)
    return graph


def root_of(graph: Graph, target_class=None):
    """The subject a self-contained shape sub-graph hangs off.

    `target_class` settles it outright: a sub-graph carries the nested shapes
    its properties reach into, and those are subjects nothing points at either
    (a property names the nested *class*, not its shape), so "the subject that
    is not an object" no longer picks one shape out of several. Attaching the
    wrong one to a component makes its config unreadable in a way that looks
    like the values were never saved.
    """
    if target_class is not None:
        for shape in graph.subjects(RDF.type, SH.NodeShape):
            if graph.value(shape, SH.targetClass) == URIRef(str(target_class)):
                return shape

    objects = {o for _, _, o in graph}
    roots = [s for s in set(graph.subjects()) if s not in objects]
    if len(roots) == 1:
        return roots[0]

    # several unreferenced subjects: prefer a shape no other shape nests into
    nested = {
        o
        for _, predicate, o in graph
        if predicate in (SH.node, SH["class"])
    }
    outer = [
        shape
        for shape in graph.subjects(RDF.type, SH.NodeShape)
        if graph.value(shape, SH.targetClass) not in nested
    ]
    if len(outer) == 1:
        return outer[0]
    if roots:
        return roots[0]
    return next(iter(graph.subjects(RDF.type, SH.NodeShape)), None)


def add_identifier(graph, subject, document):
    """The Elody document id this subject was built from.

    A definition names a component by its IRI (`prov:specializationOf
    rdfc:Validate`), which is the right thing for the toolchain but not enough
    to read the definition back: Elody addresses the same component as a
    document, `rdf-connect--shacl-processor-ts`, and that is the key a
    pipeline's `hasProcessor` relation is stored under. Carrying the id keeps
    the store a complete source of truth -- the alternative is looking every
    component up on GitHub on every read, which loses a step as soon as a
    repository moves.
    """
    data = (document or {}).get("data") or {}
    # the *component's* id, even when the document is one step of it: the step
    # is already in its IRI, and identifying the component by the step would
    # make the next read append the step again, growing the key every save
    identifier = data.get("componentId") or (document or {}).get("_id")
    if identifier:
        graph.add((subject, DCT.identifier, Literal(identifier)))


def component_iri_of(document) -> URIRef | None:
    """The IRI this component is known by, however it can be established.

    The contract catalog states it (`componentIri`); a repository discovered on
    GitHub does not, and there the class its own SHACL file targets is the same
    IRI -- that is exactly the join key the two are matched on.
    """
    data = (document or {}).get("data") or {}
    iri = data.get("componentIri")
    if iri:
        return URIRef(iri)
    shape = _ShapeIndex.from_ttl(data.get("rawTtl") or "")  # legacy: no class stored
    if shape is not None and shape.target_class:
        return URIRef(str(shape.target_class))
    return None


class ComponentCatalogSerializer:
    """Component descriptions written into one graph.

    `graph` is the document being assembled -- the definition export passes its
    own so the fragment merges into it; the catalog publisher passes nothing and
    gets a graph of its own.
    """

    def __init__(self, graph: Graph | None = None):
        self.graph = bind_prefixes(graph if graph is not None else Graph())

    # -- entry points ------------------------------------------------------

    def add(self, document, component_iri=None, shape=None):
        """Describe one component. Returns the runner it needs, or None.

        None also means "already described": the same component used by two
        steps is declared once, and the runner it needs was returned the first
        time.
        """
        g = self.graph
        component = component_iri or component_iri_of(document)
        if component is None:
            return None
        if (component, RDF.type, TCS.PipelineComponent) in g:
            return None

        data = (document or {}).get("data") or {}
        if shape is None:
            shape = _ShapeIndex.from_ttl(
                data.get("rawTtl") or "", data.get("componentIri")
            )

        if data.get("componentKind") == "dataset":
            # A dataset is a source of data, not a deployable component: it has
            # no runner, no package and nothing to install, and calling it a
            # `tcs:PipelineComponent` would offer the generator a step it
            # cannot start. It is still worth publishing -- it is the one thing
            # a DCAT catalog is actually about -- so it goes in as what it is.
            self._add_dataset(component, document, shape)
            return None

        g.add((component, RDF.type, TCS.PipelineComponent))
        g.add((component, RDF.type, DCAT.Resource))
        self._add_to_catalog(component)
        add_identifier(g, component, document)

        name = _get_metadata_value(document, "name")
        if name:
            g.add((component, RDFS.label, Literal(name)))
        description = _get_metadata_value(document, "description")
        if description:
            g.add((component, RDFS_DESCRIPTION, Literal(description)))
        url = _get_metadata_value(document, "url")
        if url:
            g.add((component, DCAT.landingPage, Literal(url)))

        runtime = _get_metadata_value(document, "runtime")
        if data.get("runnable") is False:
            # A component the catalog describes but nothing installs: Elody's
            # own alert visualisation, a semantic.works service, an LDIO
            # component. It is a step of the pipeline all the same -- its
            # contracts are what a connection into it is checked against -- but
            # it is not an RDF-Connect processor, so claiming a runner and an
            # implementation would offer the generator a step it cannot start.
            # What it needs instead is whatever the catalog says it needs (the
            # store it reads through, an orchestrator of its own framework).
            runner = None
            for required in data.get("requires") or []:
                g.add((component, DCT.requires, URIRef(required)))
        else:
            runner = RUNTIME_TO_RUNNER.get(runtime, RDFC.NodeRunner)
            g.add((component, DCT.requires, runner))
            self._add_implementation(component, document, runtime)

        deployment = data.get("deployment") or {}
        imports = deployment.get("imports") or self._fallback_imports(document)
        for target in imports:
            g.add((component, OWL.imports, URIRef(target)))

        for package in deployment.get("packages") or []:
            self._add_package(component, package)

        self._add_configs(component, data)

        self._add_shapes(component, document, shape)
        return runner

    def _add_dataset(self, dataset, document, shape):
        g = self.graph
        g.add((dataset, RDF.type, DCAT.Dataset))
        add_identifier(g, dataset, document)

        name = _get_metadata_value(document, "name")
        if name:
            g.add((dataset, RDFS.label, Literal(name)))
        description = _get_metadata_value(document, "description")
        if description:
            g.add((dataset, RDFS.comment, Literal(description)))
        url = _get_metadata_value(document, "url")
        if url:
            g.add((dataset, DCAT.landingPage, Literal(url)))

        # The shape a dataset publishes is the only thing a consumer can check
        # a downstream component against, so it travels the same way.
        self._add_shapes(dataset, document, shape)

    def add_all(self, documents) -> Graph:
        """Describe every component in `documents`, runners included."""
        runners = set()
        for document in documents:
            runner = self.add(document)
            if runner is not None:
                runners.add(runner)
        self.declare_runners(runners)
        return self.graph

    def declare_runners(self, runners):
        """A runner is a component too, and it is what needs the orchestrator.

        Emitted for the runners the components just described actually asked
        for, rather than for all of them: the catalog should not claim a
        Python runner is in play because some other component uses one.
        """
        for runner in runners:
            self.graph.add((runner, RDF.type, RDFC.Runner))
            self.graph.add((runner, RDF.type, TCS.PipelineComponent))
            self.graph.add((runner, DCT.requires, RDFC.Orchestrator))

    def serialize(self) -> str:
        normalise_iri_datatypes(self.graph)
        bind_used_namespaces(self.graph)
        return self.graph.serialize(format="turtle")

    # -- the pieces --------------------------------------------------------

    def _add_to_catalog(self, component):
        """Make `component` a resource of Elody's catalog.

        Components only, and that is not a judgement call: the same application
        profile carries `tcs:CatalogShape`, which requires *every*
        `dcat:resource` of a `tcs:Catalog` to be a `tcs:PipelineComponent`.
        Listing a dataset -- which is deliberately not one, since the generator
        cannot start it as a step -- would trade the violation this registration
        clears for a new one. A dataset is still discoverable in the catalog
        graph by its own type.

        Runners are left out for a different reason: one is emitted because a
        component requires it, nothing specialises a runner, and the toolchain's
        catalog carries the runners itself -- so listing it here would be a
        claim of ownership with nothing behind it.
        """
        catalog = catalog_iri()
        self.graph.add((catalog, RDF.type, TCS.Catalog))
        self.graph.add((catalog, DCAT.resource, component))

    def _add_configs(self, component, data):
        """How the component is deployed, as the catalog declares it.

        `tcs:config` and the config nodes themselves, copied verbatim. The
        application profile requires every `tcs:PipelineComponent` to reach a
        `tcs:DockerComposeConfig` along `dct:requires*`, so a component that
        has one and does not say so is a component the generator will not
        compile ("PipelineComponent {?this} is not deployable").
        """
        config_ttl = data.get("configTtl")
        if config_ttl:
            configs = Graph()
            try:
                configs.parse(data=config_ttl, format="turtle")
            except Exception:
                configs = Graph()
            self.graph += configs
        for config in data.get("configs") or []:
            self.graph.add((component, TCS.config, URIRef(config)))

    def _add_implementation(self, component, document, runtime):
        """`<component> rdfc:jsImplementationOf rdfc:Processor`, or nothing.

        Read off the component's own processor file when it has one, so the
        predicate is the runtime's rather than a guess; a catalog-declared
        component gets it from its runtime.
        """
        raw_ttl = ((document or {}).get("data") or {}).get("rawTtl") or ""
        if raw_ttl:
            source = Graph()
            try:
                source.parse(data=raw_ttl, format="turtle")
            except Exception:
                source = Graph()
            for predicate in set(source.predicates(component, RDFC.Processor)):
                if str(predicate).split("#")[-1].endswith("ImplementationOf"):
                    self.graph.add((component, predicate, RDFC.Processor))
                    return

        predicate = RUNTIME_TO_IMPLEMENTATION.get((runtime or "").lower())
        if predicate is not None:
            self.graph.add((component, predicate, RDFC.Processor))

    def _fallback_imports(self, document):
        """Where the shapes were read from, when the catalog names nothing.

        A processor discovered on GitHub carries no package coordinates, so the
        only honest import target is the file the shapes came from. It is not
        what a built container would use, but it resolves and it names the
        right document.
        """
        owner = _get_metadata_value(document, "owner")
        branch = _get_metadata_value(document, "defaultBranch") or "main"
        files = _get_metadata_value(document, "shaclFiles") or ""
        repo = ((document or {}).get("_id") or "").split("--")[-1]
        if not owner or not repo or not files:
            return []
        return [
            f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
            for path in files.split(",")
            if path
        ]

    def _add_package(self, component, package):
        name = package.get("name")
        if not name:
            return
        node = BNode()
        self.graph.add((component, DCT.requires, node))
        self.graph.add((node, RDF.type, SPDX.Package))
        self.graph.add((node, SPDX.name, Literal(name)))
        if package.get("version"):
            self.graph.add((node, SPDX.versionInfo, Literal(package["version"])))
        if package.get("supplier"):
            self.graph.add((node, SPDX.suppliedBy, URIRef(package["supplier"])))

    def _add_shapes(self, component, document, shape):
        """Attach the component's shapes in their discovery-vocabulary roles.

        The config shape is what the generator's own catalog carries; the input
        and output shapes are what the toolchain's shape-matching test suite
        needs, and are the same ones Elody validated the chain against.
        """
        data = (document or {}).get("data") or {}

        for key, role in SHAPE_ROLES.items():
            declared = data.get(key)
            if declared:
                self._attach_shape(
                    component, role, declared.get("ttl"), declared.get("iri")
                )
            elif key == "configShape":
                self._attach_config_from_raw_ttl(component, document, shape)

    def _attach_config_from_raw_ttl(self, component, document, shape):
        """Fall back to the processor's own SHACL file for the config shape."""
        if shape is None:
            return
        source = Graph()
        try:
            source.parse(
                data=((document or {}).get("data") or {}).get("rawTtl") or "",
                format="turtle",
            )
        except Exception:
            return
        node = next(
            (
                s
                for s in source.subjects(RDF.type, SH.NodeShape)
                if source.value(s, SH.targetClass) == shape.target_class
            ),
            None,
        )
        if node is None:
            return
        self._attach_shape(
            component,
            TCS.configShape,
            extract_shape_graph(source, node).serialize(format="turtle"),
            str(node) if isinstance(node, URIRef) else None,
            # the class this shape is for, so the nested shapes travelling with
            # it cannot be mistaken for the component's own
            target_class=shape.target_class,
        )

    def _attach_shape(self, component, role, ttl, iri, target_class=None):
        if not ttl:
            return
        shape_graph = Graph()
        try:
            shape_graph.parse(data=ttl, format="turtle")
        except Exception:
            return

        target = URIRef(iri) if iri else root_of(shape_graph, target_class)
        if target is None:
            return

        # carry the shape's own prefixes over, so the merged document still
        # reads as `demo:MeasurementsInCmShape` rather than `ns1:...`
        for prefix, namespace in shape_graph.namespaces():
            self.graph.bind(prefix, namespace, replace=False)

        relation = BNode()
        self.graph.add((component, DCAT.qualifiedRelation, relation))
        self.graph.add((relation, RDF.type, DCAT.Relationship))
        self.graph.add((relation, DCAT.hadRole, role))
        self.graph.add((relation, DCT.relation, target))
        self.graph += shape_graph
