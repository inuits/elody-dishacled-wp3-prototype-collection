import re
from os import getenv

from rdflib import BNode, Graph, Literal, Namespace, RDF, URIRef

from apps.dishacled.pipeline.connections import (
    connections_for_pipeline,
    instances_of,
    nest_metadata,
)
from apps.dishacled.shacl.graphs import parsed_graph

RDFC = Namespace("https://w3id.org/rdf-connect#")

OWL = Namespace("http://www.w3.org/2002/07/owl#")

RUNTIME_TO_RUNNER = {
    "ts": RDFC.NodeRunner,
    "py": RDFC.PyRunner,
    "jvm": RDFC.JvmRunner,
}

# Where each runner's own definition lives, relative to the pipeline file. The
# runner is not a component -- nothing discovers it -- so the pipeline has to
# name it, and without it the runner class is undefined and the orchestrator
# starts nothing (the same defect as the toolchain generator's, see
# `docs/toolchain-open-questions.md` section 7).
#
# Only the Node one is knowable: `@rdfc/js-runner` publishes `index.ttl` at a
# fixed path inside the package. A Python runner's path carries the interpreter
# version and a JVM runner's is whatever the build produced, so those are
# configuration -- an invented path is a broken import, which is worse than a
# missing one.
def _runner_imports() -> dict:
    return {
        RDFC.NodeRunner: getenv(
            "RDFC_NODE_RUNNER_IMPORT", "./node_modules/@rdfc/js-runner/index.ttl"
        ).strip(),
        RDFC.PyRunner: getenv("RDFC_PY_RUNNER_IMPORT", "").strip(),
        RDFC.JvmRunner: getenv("RDFC_JVM_RUNNER_IMPORT", "").strip(),
    }

SH = Namespace("http://www.w3.org/ns/shacl#")

# The package-manager IRIs the manifests are read into
# (`storage/dishacled_httpstore.py`), and the command that installs each.
INSTALLERS = (
    ("http://example.org/example/npm", "npm install", "@"),
    ("http://example.org/example/pip", "pip install", ""),
)
XSD_STRING = URIRef("http://www.w3.org/2001/XMLSchema#string")
# Not a real XSD datatype, but what the RDF-Connect catalogs use to say "this
# parameter is an IRI": `sdsify`'s typeFilter and streamId, the threshold
# monitor's creator and path. It stays a *typed literal* on the way out, which
# is both what the toolchain's application profile requires ("Value is not
# Literal with datatype xsd:iri" is a violation it raises on an actual IRI) and
# what the orchestrator reads -- it maps the datatype to JSON-LD `@id`, so the
# literal's lexical form becomes the id. Verified both ways by running the same
# pipeline with each spelling: the monitor starts and alerts arrive either way.
XSD_IRI = URIRef("http://www.w3.org/2001/XMLSchema#iri")
XSD_IRI_NOTE = """\
`xsd:iri` is not a real datatype. It is how the RDF-Connect processors say "an
IRI" (`sdsify`'s typeFilter and streamId, the threshold monitor's creator and
path), and the toolchain's harvested catalog spells the same thing out as
`sh:nodeKind sh:IRI ; tcs:upstreamDatatype xsd:iri` -- so an IRI node is what a
config value has to be, and its own validation report says so
("Value is not of Node Kind sh:IRI"). The orchestrator agrees: it maps the
datatype to JSON-LD `@id`.
"""

CHANNEL_CLASSES = {RDFC.Reader, RDFC.Writer, RDFC.Channel}


def _slugify(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _get_metadata_value(entity, key):
    for item in entity.get("metadata", []):
        if item.get("key") == key:
            return item.get("value")
    return None


class _ShapeIndex:
    """Property bindings for one processor shape, with nested shapes resolved.

    `properties` maps sh:name -> {path, datatype, class, nested}. A property
    whose sh:class / sh:node points to another local NodeShape carries that
    shape as `nested` (an _ShapeIndex), recursively — so nested config
    (shui:DetailsEditor) round-trips into nested RDF-Connect blank nodes.
    """

    def __init__(self, target_class, properties):
        self.target_class = target_class
        self.properties = properties  # name -> {path, datatype, class, nested}

    @classmethod
    def from_ttl(cls, raw_ttl, target_class=None):
        """The shape of one processor class in this TTL.

        `target_class` is the class the component says it is
        (`data.componentIri`), and it is the only reliable answer for a file
        that declares several processors: which one a guess lands on is not
        knowable from the file. It is a fallback, not a requirement, so a
        component stored before components carried their class still exports.
        """
        # a processor with unparseable TTL should not break the whole
        # pipeline export; its stage is skipped
        g = parsed_graph(raw_ttl)
        if g is None:
            return None

        shapes_by_class = {}
        for node_shape in g.subjects(RDF.type, SH.NodeShape):
            shape_target = g.value(node_shape, SH.targetClass)
            if shape_target is not None:
                shapes_by_class[shape_target] = node_shape

        main_class = None
        if target_class is not None:
            candidate = URIRef(str(target_class))
            if candidate in shapes_by_class:
                main_class = candidate

        if main_class is None:
            # The processor class is the subject of a `rdfc:*ImplementationOf
            # rdfc:Processor` triple (e.g. rdfc:HttpFetch). Prefer the NodeShape
            # whose sh:targetClass is that class, so multi-shape processor files
            # (HttpFetch + HttpFetchAuth + HttpFetchOptions) bind the right
            # shape. Sorted, so a file with several processors resolves the same
            # way in every process -- and the same way the form derivation does.
            processor_classes = sorted(
                (
                    s
                    for s, p, o in g
                    if o == RDFC.Processor
                    and str(p).split("#")[-1].endswith("ImplementationOf")
                ),
                key=str,
            )
            main_class = next(
                (c for c in processor_classes if c in shapes_by_class), None
            )
        if main_class is None:
            main_class = next(iter(sorted(shapes_by_class, key=str)), None)
        if main_class is None:
            return None

        return cls._build(g, main_class, shapes_by_class, {main_class})

    @classmethod
    def _build(cls, g, target_class, shapes_by_class, visited):
        node_shape = shapes_by_class[target_class]
        properties = {}
        for prop_node in g.objects(node_shape, SH.property):
            name = g.value(prop_node, SH.name)
            path = g.value(prop_node, SH.path)
            if not name or not path:
                continue
            class_ref = g.value(prop_node, SH["class"])
            node_ref = g.value(prop_node, SH.node)

            nested = None
            nested_class = None
            if node_ref is not None:
                nested_class = g.value(node_ref, SH.targetClass)
            elif (
                class_ref is not None
                and class_ref not in CHANNEL_CLASSES
                and class_ref in shapes_by_class
            ):
                nested_class = class_ref
            if (
                nested_class is not None
                and nested_class in shapes_by_class
                and nested_class not in visited
            ):
                nested = cls._build(
                    g, nested_class, shapes_by_class, visited | {nested_class}
                )

            properties[str(name)] = {
                "path": path,
                "datatype": g.value(prop_node, SH.datatype),
                "nodeKind": g.value(prop_node, SH.nodeKind),
                "class": class_ref,
                "nested": nested,
            }
        return cls(target_class, properties)


def shape_index_for(graph, target_class, shapes_by_class=None):
    """The property bindings of one shape already present in a graph.

    `_ShapeIndex.from_ttl` picks its own shape out of a processor file, by
    looking for the class the file declares an implementation of. A pipeline
    definition holds many shapes and declares no implementation, so the caller
    names the class instead -- reading a definition back needs exactly the
    bindings the export wrote it with, and this is where they are computed.
    """
    if shapes_by_class is None:
        shapes_by_class = {
            target: shape
            for shape in graph.subjects(RDF.type, SH.NodeShape)
            if (target := graph.value(shape, SH.targetClass)) is not None
        }
    if target_class not in shapes_by_class:
        return None
    return _ShapeIndex._build(graph, target_class, shapes_by_class, {target_class})


def shape_index_for_shape_node(graph, shape_node, shapes_by_class=None):
    """The property bindings of one shape, addressed by its node.

    `shape_index_for` addresses a shape by its `sh:targetClass`, which every
    shape the codegen writes carries. A shape authored by hand in the shared
    catalog -- the anonymous config shape that only declares a component's
    reader/writer ports -- often has none, and reading a definition back must
    still resolve its port names, or the connections bound through those ports
    silently disappear from the entity. The shape node itself is the index key
    then; nothing downstream reads the target class of the root shape.
    """
    if shape_node is None:
        return None
    if shapes_by_class is None:
        shapes_by_class = {
            target: shape
            for shape in graph.subjects(RDF.type, SH.NodeShape)
            if (target := graph.value(shape, SH.targetClass)) is not None
        }
    key = graph.value(shape_node, SH.targetClass) or shape_node
    return _ShapeIndex._build(
        graph, key, {**shapes_by_class, key: shape_node}, {key}
    )


def _is_iri_valued(binding) -> bool:
    return binding.get("datatype") == XSD_IRI or binding.get("nodeKind") == SH.IRI


def emit_config_values(g, subject, shape, values, base_uri, channels):
    """Emit one shape's config values onto `subject`, recursing into nested nodes.

    Shared by both exports: the RDF-Connect pipeline puts these directly on the
    stage, the toolchain pipeline definition puts them inside a `tcs:embedded`
    block, but the mapping from a form value to a predicate, a datatype and a
    channel IRI is the same in either case.
    """
    for name, binding in shape.properties.items():
        if name not in values:
            continue
        value = values[name]

        if binding["nested"] is not None:
            if not isinstance(value, dict):
                continue
            nested_node = BNode()
            emit_config_values(
                g, nested_node, binding["nested"], value, base_uri, channels
            )
            if binding["class"] is not None:
                # The shape says the value has to be of this class
                # (`sh:class rdfc:IngestConfig`), and an untyped blank node is
                # a violation the toolchain's profile reports as "Value does
                # not have class rdfc:IngestConfig". It also tells the runner
                # which shape to read the nested config with.
                g.add((nested_node, RDF.type, binding["class"]))
            # only attach if the nested node carries any triples
            if next(g.predicate_objects(nested_node), None) is not None:
                g.add((subject, binding["path"], nested_node))
            continue

        if value in (None, ""):
            continue

        if binding["class"] in CHANNEL_CLASSES:
            channel_uri = URIRef(base_uri + _slugify(value))
            g.add((subject, binding["path"], channel_uri))
            channels.add(channel_uri)
        elif _is_iri_valued(binding):
            # see XSD_IRI_NOTE
            g.add((subject, binding["path"], URIRef(str(value))))
        else:
            datatype = binding["datatype"]
            if datatype == XSD_STRING:
                datatype = None  # plain literal, identical in RDF 1.1
            g.add((subject, binding["path"], Literal(value, datatype=datatype)))


class PipelineTtlSerializer:
    """Serialize an Elody pipeline entity graph to a runnable RDF-Connect pipeline.ttl.

    Config values are read from the metadata on the pipeline's hasProcessor
    relations; predicates and datatypes are recovered from the SHACL shape
    stored as rawTtl on each githubProcessor entity.
    """

    def __init__(self, base_uri: str = ""):
        # Document-relative on purpose. The pipeline names itself `<>` and its
        # stages and channels by bare name, which is what both files known to
        # run look like: the tutorial's hand-written `pipeline.ttl` and the
        # toolchain generator's output. It makes the file work wherever it is
        # put -- and, unlike an absolute Elody IRI, `<>` is the subject a reader
        # resolves the document's own `owl:imports` against, so there is no
        # question of the runner looking somewhere we did not write them.
        #
        # `base_uri` is accepted because both exports share one call signature
        # (`resources/pipeline_export.py`); it is deliberately not used to build
        # IRIs here. The *definition* export is the one whose IRIs have to be
        # absolute, because it is published into a shared store.
        self.base_uri = base_uri
        # keys of `hasProcessor` relations that produced no stage
        self.unrepresented: list = []

    def serialize(self, pipeline, processors):
        self.unrepresented = []
        self._imported: list = []
        g = Graph()
        g.bind("rdfc", RDFC)

        pipeline_uri = URIRef("")  # the document itself
        g.add((pipeline_uri, RDF.type, RDFC.Pipeline))

        runner_groups = {}  # runner class URIRef -> [stage URIRef]
        channels = set()
        stages = {}  # processor key -> (stage URIRef, _ShapeIndex)

        for instance in instances_of(pipeline, processors):
            relation = instance.relation
            key = instance.key
            processor = processors.get(key)
            if not processor:
                self._unrepresented(key)
                continue

            data = processor.get("data") or {}
            raw_ttl = data.get("rawTtl")
            if not raw_ttl:
                self._unrepresented(key)
                continue
            # the class the component says it is, not the one a multi-processor
            # file happens to yield first
            shape = _ShapeIndex.from_ttl(raw_ttl, data.get("componentIri"))
            if not shape:
                self._unrepresented(key)
                continue

            # named for the step, not the component: two steps of one
            # component are two stages with configurations of their own
            stage_uri = URIRef(instance.id)
            g.add((stage_uri, RDF.type, shape.target_class))
            stages[instance.id] = (stage_uri, shape)

            # A dataset is a source of data, not an implementation, so it is
            # emitted as a stage that can be wired but is never handed to a
            # runner to instantiate. The same holds for a component nothing
            # installs (`runnable` in shacl/contracts.py) -- Elody's alert
            # visualisation reads the store the pipeline writes to, so it is a
            # stage of the chain, but no runner can start it.
            is_dataset = data.get("componentKind") == "dataset"
            if not is_dataset and data.get("runnable") is not False:
                runner = RUNTIME_TO_RUNNER.get(
                    _get_metadata_value(processor, "runtime"), RDFC.NodeRunner
                )
                runner_groups.setdefault(runner, []).append(stage_uri)

            # Flat dotted-key metadata (url, options.method, options.auth.type)
            # -> nested value dict, then emit following the (nested) shape.
            values = nest_metadata(relation.get("metadata", []))
            self._emit_values(g, stage_uri, shape, values, channels)

        self._emit_connections(g, pipeline, processors, stages, channels)

        self._emit_imports(g, pipeline_uri, runner_groups, processors)

        for runner, stages_of_runner in runner_groups.items():
            group = BNode()
            g.add((pipeline_uri, RDFC.consistsOf, group))
            g.add((group, RDFC.instantiates, runner))
            for stage in stages_of_runner:
                g.add((group, RDFC.processor, stage))

        for channel_uri in channels:
            g.add((channel_uri, RDF.type, RDFC.Reader))
            g.add((channel_uri, RDF.type, RDFC.Writer))

        header = self._install_header(processors, self._imported)
        return header + g.serialize(format="turtle")

    def _install_header(self, processors, targets) -> str:
        """What has to exist next to this file, as turtle comments.

        The pipeline imports each processor's definition from inside its
        installed package. When one is missing the orchestrator says
        `ENOENT ... processors.ttl` and stops -- a true statement about a path,
        with no hint of where it should have come from. Elody read these
        coordinates off the repositories to build the imports in the first
        place, so it can say them.
        """
        by_installer: dict = {}
        for document in processors.values():
            deployment = (document.get("data") or {}).get("deployment") or {}
            for package in deployment.get("packages") or []:
                name = package.get("name")
                if not name:
                    continue
                for supplier, command, joiner in INSTALLERS:
                    if package.get("supplier") == supplier:
                        version = package.get("version") or ""
                        by_installer.setdefault(command, set()).add(
                            f"{name}{joiner}{version}" if version else name
                        )

        # imports nothing installs: a jvm jar a build produces, say. Remote ones
        # resolve themselves, so they are not somebody's homework.
        unexplained = sorted(
            target
            for target in targets
            if not target.startswith(("http://", "https://"))
            and not target.startswith("./node_modules/")
        )

        lines = [
            "# Runnable RDF-Connect pipeline, exported from Elody.",
            "#",
            "# Run it from the directory this file is in -- its imports and file",
            "# paths are relative to it:",
            "#",
            "#   npx rdfc " + "<this file>",
        ]
        if by_installer or unexplained:
            lines += ["#", "# It needs these alongside it:"]
        for _supplier, command, _joiner in INSTALLERS:
            packages = by_installer.get(command)
            if packages:
                lines += ["#", f"#   {command} " + " ".join(sorted(packages))]
        if unexplained:
            lines += ["#", "#   and, from your own build:"]
            lines += [f"#     {target}" for target in unexplained]
        return "\n".join(lines) + "\n\n"

    def _emit_imports(self, g, pipeline_uri, runner_groups, processors):
        """`owl:imports` for the runners in use and every processor definition.

        Relative on purpose: the orchestrator resolves them against the
        directory the pipeline file is in, and an absolute IRI is not something
        its importer follows (section 7 again). Order is stable and the runners
        come first, the way the hand-built demonstrator pipeline writes them.
        """
        g.bind("owl", OWL)
        targets = []

        runner_imports = _runner_imports()
        for runner in runner_groups:
            target = runner_imports.get(runner)
            if target and target not in targets:
                targets.append(target)

        for document in processors.values():
            deployment = (document.get("data") or {}).get("deployment") or {}
            for target in deployment.get("imports") or []:
                if target and target not in targets:
                    targets.append(target)

        for target in targets:
            g.add((pipeline_uri, OWL.imports, URIRef(target)))
        self._imported = list(targets)

    def _unrepresented(self, key):
        if key and key not in self.unrepresented:
            self.unrepresented.append(key)

    def _emit_connections(self, g, pipeline, processors, stages, channels):
        """Bind each declared connection's two ports to one shared channel.

        The connection is authoritative: a channel value typed into the config
        form by hand is replaced, so the exported pipeline always reflects the
        links the user drew rather than two names that may have drifted apart.
        """
        for connection in connections_for_pipeline(pipeline, processors):
            source = stages.get(connection.source)
            target = stages.get(connection.target)
            if not source or not target:
                continue

            source_uri, source_shape = source
            target_uri, target_shape = target
            source_path = (source_shape.properties.get(connection.source_port) or {}).get(
                "path"
            )
            target_path = (target_shape.properties.get(connection.target_port) or {}).get(
                "path"
            )
            if source_path is None or target_path is None:
                continue

            channel_uri = URIRef(_slugify(connection.channel))
            g.remove((source_uri, source_path, None))
            g.add((source_uri, source_path, channel_uri))
            g.remove((target_uri, target_path, None))
            g.add((target_uri, target_path, channel_uri))
            channels.add(channel_uri)

    def _emit_values(self, g, subject, shape, values, channels):
        emit_config_values(g, subject, shape, values, "", channels)


