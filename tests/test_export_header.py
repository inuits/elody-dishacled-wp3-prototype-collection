"""The runnable export says what has to be installed next to it.

The pipeline imports each processor's definition from inside its installed
package, so a missing package is not a pipeline problem -- it is an ENOENT from
the orchestrator, naming a path with no explanation of where it should have come
from. Elody read those coordinates off the repository manifests to build the
imports; writing them into the file as well turns "no such file or directory"
into a line to copy.

Comments, so the document is exactly the same graph either way.
"""

from rdflib import Graph

from apps.dishacled.serializers.pipeline_ttl_serializer import (
    PipelineTtlSerializer,
)

from tests.test_validation import make_pipeline, processor_relation


TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh:   <http://www.w3.org/ns/shacl#>.

rdfc:{cls} rdfc:{impl} rdfc:Processor.

[] a sh:NodeShape;
  sh:targetClass rdfc:{cls};
  sh:property [ sh:class rdfc:Writer; sh:path rdfc:writer; sh:name "writer" ].
"""

NPM = "http://example.org/example/npm"
PIP = "http://example.org/example/pip"


def component(name, cls, runtime, imports, packages, impl="jsImplementationOf"):
    return {
        "_id": name,
        "type": "githubProcessor",
        "metadata": [
            {"key": "name", "value": cls},
            {"key": "runtime", "value": runtime},
        ],
        "data": {
            "rawTtl": TTL.format(cls=cls, impl=impl),
            "componentIri": f"https://w3id.org/rdf-connect#{cls}",
            "deployment": {"imports": imports, "packages": packages},
        },
    }


FETCH = component(
    "a--http--HttpFetch", "HttpFetch", "ts",
    ["./node_modules/@rdfc/http-utils-processor-ts/processors.ttl"],
    [{"name": "@rdfc/http-utils-processor-ts", "version": "^1.0.2", "supplier": NPM}],
)
GLOB = component(
    "a--file--GlobRead", "GlobRead", "ts",
    ["./node_modules/@rdfc/file-utils-processors-ts/processors.ttl"],
    [{"name": "@rdfc/file-utils-processors-ts", "version": "^1.0.1", "supplier": NPM}],
)
TRANSLATOR = component(
    "a--translate--Translate", "Translate", "py", [],
    [{"name": "rdfc-translation", "version": ">=0.1.0", "supplier": PIP}],
    impl="pyImplementationOf",
)
MAPPER = component(
    "a--rml--RmlMapper", "RmlMapper", "jvm",
    ["./build/plugins/rml-processor-jvm-master-SNAPSHOT-all.jar"], [],
    impl="jvmImplementationOf",
)
REMOTE = component(
    "a--remote--Remote", "Remote", "jvm",
    ["https://javadoc.jitpack.io/some/runner-index.jar"], [],
    impl="jvmImplementationOf",
)


def export(*components):
    documents = {c["_id"]: c for c in components}
    return PipelineTtlSerializer().serialize(
        make_pipeline([processor_relation(key) for key in documents]), documents
    )


def header(ttl):
    return "\n".join(
        line for line in ttl.splitlines() if line.startswith("#")
    )


class TestWhatToInstall:
    def test_npm_packages_are_listed_with_their_versions(self):
        text = header(export(FETCH, GLOB))
        assert "npm install" in text
        assert "@rdfc/http-utils-processor-ts@^1.0.2" in text
        assert "@rdfc/file-utils-processors-ts@^1.0.1" in text

    def test_the_npm_line_is_one_line_and_sorted(self):
        line = next(
            l for l in header(export(FETCH, GLOB)).splitlines()
            if "npm install" in l
        )
        assert line.index("@rdfc/file-utils") < line.index("@rdfc/http-utils")

    def test_pip_packages_get_their_own_line(self):
        text = header(export(TRANSLATOR))
        assert "pip install" in text
        assert "rdfc-translation>=0.1.0" in text

    def test_no_npm_line_when_nothing_comes_from_npm(self):
        assert "npm install" not in header(export(TRANSLATOR))

    def test_a_local_import_no_package_covers_is_called_out(self):
        # the jvm jar: `gradle copyPlugins` produces it, and nothing Elody can
        # read says so
        text = header(export(MAPPER))
        assert "./build/plugins/rml-processor-jvm-master-SNAPSHOT-all.jar" in text

    def test_a_remote_import_is_not_called_out(self):
        # an http(s) import resolves itself; asking someone to provide it would
        # be noise
        assert "javadoc.jitpack.io" not in header(export(REMOTE))

    def test_a_pipeline_with_nothing_to_install_says_nothing(self):
        bare = component("a--bare--Bare", "Bare", "ts", [], [])
        text = header(export(bare))
        assert "npm install" not in text and "pip install" not in text


class TestItIsOnlyComments:
    def test_the_document_still_parses(self):
        graph = Graph()
        graph.parse(data=export(FETCH, MAPPER), format="turtle", publicID="file:///p/")
        assert len(graph) > 0

    def test_the_graph_is_unchanged_by_the_header(self):
        ttl = export(FETCH, GLOB)
        with_header = Graph()
        with_header.parse(data=ttl, format="turtle", publicID="file:///p/")

        without = Graph()
        without.parse(
            data="\n".join(
                line for line in ttl.splitlines() if not line.startswith("#")
            ),
            format="turtle",
            publicID="file:///p/",
        )
        assert len(with_header) == len(without)
