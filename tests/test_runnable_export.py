"""`export.ttl` has to be a pipeline the runner can actually start.

`npx rdfc <file>` reads the file, looks for a `rdfc:Pipeline`, and instantiates
the processors its runners declare. It can only do that if the file *imports*
the definitions of those processors and of the runner itself -- otherwise
`rdfc:HttpFetch` and `rdfc:NodeRunner` are just IRIs nothing describes, and the
orchestrator binds its gRPC port and sits there having started nothing.

Elody knows the imports: each component carries `deployment.imports`, read off
the repository's own manifest (`storage/dishacled_httpstore.py`). The runner's
own definition is not a component, so it is named here, per runtime.
"""

from apps.dishacled.serializers.pipeline_ttl_serializer import (
    PipelineTtlSerializer,
)

from tests.test_validation import make_pipeline, processor_relation


PROCESSOR_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh:   <http://www.w3.org/ns/shacl#>.
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#>.

rdfc:{cls} rdfc:{impl} rdfc:Processor.

[] a sh:NodeShape;
  sh:targetClass rdfc:{cls};
  sh:property [
    sh:class rdfc:Writer; sh:path rdfc:writer; sh:name "writer"; sh:maxCount 1;
  ].
"""


def component(identifier, cls, runtime="ts", imports=(), impl="jsImplementationOf"):
    return {
        "_id": identifier,
        "type": "githubProcessor",
        "metadata": [
            {"key": "name", "value": cls},
            {"key": "runtime", "value": runtime},
        ],
        "data": {
            "rawTtl": PROCESSOR_TTL.format(cls=cls, impl=impl),
            "componentIri": f"https://w3id.org/rdf-connect#{cls}",
            "deployment": {"imports": list(imports), "packages": []},
        },
    }


FETCH = component(
    "rdf-connect--http-utils-processor-ts--HttpFetch",
    "HttpFetch",
    imports=["./node_modules/@rdfc/http-utils-processor-ts/processors.ttl"],
)
GLOB = component(
    "rdf-connect--file-utils-processors-ts--GlobRead",
    "GlobRead",
    imports=["./node_modules/@rdfc/file-utils-processors-ts/processors.ttl"],
)
# same repository, so the same file: one import, not two
ENVSUB = component(
    "rdf-connect--file-utils-processors-ts--Envsub",
    "Envsub",
    imports=["./node_modules/@rdfc/file-utils-processors-ts/processors.ttl"],
)
MAPPER = component(
    "rdf-connect--rml-processor-jvm--RmlMapper",
    "RmlMapper",
    runtime="jvm",
    impl="jvmImplementationOf",
)
TRANSLATOR = component(
    "local--translator",
    "TranslationProcessor",
    runtime="py",
    impl="pyImplementationOf",
)
NO_COORDINATES = component("local--homegrown", "Homegrown")


def export(components, relations=None):
    relations = relations or [processor_relation(key) for key in components]
    return PipelineTtlSerializer(base_uri="http://x/p1/").serialize(
        make_pipeline(relations), components
    )


# Where the runner reads the pipeline from. Parsing with this base is what
# makes a relative `owl:imports` mean "next to the pipeline file" -- parse
# without one and rdflib resolves it against the current directory instead,
# which is a property of the test, not of the document.
RUNTIME_BASE = "file:///workspace/pipeline/"


def imports_of(ttl):
    from rdflib import Graph
    from rdflib.namespace import OWL

    graph = Graph()
    graph.parse(data=ttl, format="turtle", publicID=RUNTIME_BASE)
    return sorted(
        str(target).replace(RUNTIME_BASE, "./")
        for target in graph.objects(None, OWL.imports)
    )


class TestTheProcessorsAreImported:
    def test_a_components_own_definition_is_imported(self):
        ttl = export({FETCH["_id"]: FETCH})
        assert (
            "./node_modules/@rdfc/http-utils-processor-ts/processors.ttl"
            in imports_of(ttl)
        )

    def test_every_component_contributes_its_own(self):
        ttl = export({FETCH["_id"]: FETCH, GLOB["_id"]: GLOB})
        assert (
            "./node_modules/@rdfc/file-utils-processors-ts/processors.ttl"
            in imports_of(ttl)
        )

    def test_two_processors_from_one_repository_import_it_once(self):
        ttl = export({GLOB["_id"]: GLOB, ENVSUB["_id"]: ENVSUB})
        shared = "./node_modules/@rdfc/file-utils-processors-ts/processors.ttl"
        assert imports_of(ttl).count(shared) == 1

    def test_a_component_without_coordinates_contributes_none(self):
        # a processor Elody could not read a manifest for: nothing to import,
        # and nothing that should break the export
        ttl = export({NO_COORDINATES["_id"]: NO_COORDINATES})
        assert imports_of(ttl) == ["./node_modules/@rdfc/js-runner/index.ttl"]

    def test_the_imports_hang_off_the_document(self):
        """`<>`, which is what the hand-built and generated pipelines both use.

        It is also the only subject a reader can resolve without being told
        where the pipeline came from -- an absolute Elody IRI would be a
        different subject from the document the runner loaded, and an importer
        keyed on the document would find nothing.
        """
        from rdflib import Graph, URIRef
        from rdflib.namespace import OWL

        ttl = export({FETCH["_id"]: FETCH})
        assert "<> a rdfc:Pipeline" in ttl

        graph = Graph()
        graph.parse(data=ttl, format="turtle", publicID=RUNTIME_BASE)
        assert list(graph.objects(URIRef(RUNTIME_BASE), OWL.imports))


class TestTheRunnerIsImportedToo:
    """Without it `rdfc:NodeRunner` is undefined and zero processors start.

    This is the first of the two defects the toolchain generator has as well
    (`docs/toolchain-open-questions.md` section 7) -- so Elody's own runnable
    export must not repeat it.
    """

    def test_the_node_runner_is_imported_for_a_ts_processor(self):
        assert "./node_modules/@rdfc/js-runner/index.ttl" in imports_of(
            export({FETCH["_id"]: FETCH})
        )

    def test_it_is_imported_once_for_several_processors(self):
        ttl = export({FETCH["_id"]: FETCH, GLOB["_id"]: GLOB})
        assert imports_of(ttl).count("./node_modules/@rdfc/js-runner/index.ttl") == 1

    def test_a_jvm_runner_is_not_guessed(self, monkeypatch):
        # the jar's path depends on how the project was built; inventing one
        # would be a broken import rather than a missing one
        monkeypatch.delenv("RDFC_JVM_RUNNER_IMPORT", raising=False)
        assert imports_of(export({MAPPER["_id"]: MAPPER})) == []

    def test_a_jvm_runner_import_can_be_configured(self, monkeypatch):
        monkeypatch.setenv(
            "RDFC_JVM_RUNNER_IMPORT", "./build/plugins/jvm-runner-all.jar"
        )
        assert "./build/plugins/jvm-runner-all.jar" in imports_of(
            export({MAPPER["_id"]: MAPPER})
        )

    def test_a_python_runner_is_not_guessed(self, monkeypatch):
        # its install path carries the interpreter version, which is not
        # knowable from here -- the same reason no pip import is synthesised
        monkeypatch.delenv("RDFC_PY_RUNNER_IMPORT", raising=False)
        assert imports_of(export({TRANSLATOR["_id"]: TRANSLATOR})) == []

    def test_a_python_runner_import_can_be_configured(self, monkeypatch):
        monkeypatch.setenv(
            "RDFC_PY_RUNNER_IMPORT",
            "./.venv/lib/python3.13/site-packages/rdfc_runner/index.ttl",
        )
        assert (
            "./.venv/lib/python3.13/site-packages/rdfc_runner/index.ttl"
            in imports_of(export({TRANSLATOR["_id"]: TRANSLATOR}))
        )

    def test_only_the_runners_in_use_are_imported(self, monkeypatch):
        monkeypatch.setenv("RDFC_PY_RUNNER_IMPORT", "./py-runner/index.ttl")
        ttl = export({FETCH["_id"]: FETCH})
        assert "./py-runner/index.ttl" not in imports_of(ttl)


class TestTheDocumentStillParses:
    def test_a_relative_import_survives_a_round_trip(self):
        # rdflib resolves relative IRIs at parse time, so the export must write
        # them in a form that comes back relative -- the orchestrator's importer
        # only follows those
        ttl = export({FETCH["_id"]: FETCH})
        assert "<./node_modules/@rdfc/http-utils-processor-ts/processors.ttl>" in ttl

    def test_the_pipeline_is_still_an_rdfc_pipeline(self):
        assert "a rdfc:Pipeline" in export({FETCH["_id"]: FETCH})
