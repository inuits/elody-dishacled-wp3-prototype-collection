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
"""

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
        runner = RUNTIME_TO_RUNNER.get(runtime, RDFC.NodeRunner)
        g.add((component, DCT.requires, runner))
        self._add_implementation(component, document, runtime)

        deployment = data.get("deployment") or {}
        imports = deployment.get("imports") or self._fallback_imports(document)
        for target in imports:
            g.add((component, OWL.imports, URIRef(target)))

        for package in deployment.get("packages") or []:
            self._add_package(component, package)

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
        return self.graph.serialize(format="turtle")

    # -- the pieces --------------------------------------------------------

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
