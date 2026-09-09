"""One component per processor class, not one per repository.

A repository may declare several processors: `file-utils-processors-ts` holds
GlobRead, FolderRead, Envsub and five more. Identifying a component with its
repository therefore loses seven of eight, and *which* one survived was decided
by iteration order over a set -- unstable between processes, and decided
separately by the form derivation and by the export, so the two could disagree
and the config a user typed was silently dropped.

So a component is a processor class in a repository, and the class it is
travels with the document (`data.componentIri`). Everything that needs a shape
takes it from there instead of guessing.
"""

import base64
from unittest.mock import MagicMock

from apps.dishacled.pipeline.connections import ports_for_component
from apps.dishacled.serializers.pipeline_definition_serializer import (
    PipelineDefinitionSerializer,
)
from apps.dishacled.serializers.pipeline_ttl_serializer import (
    PipelineTtlSerializer,
    _ShapeIndex,
)
from apps.dishacled.shacl.contracts import (
    component_iri_from_ttl,
    processor_classes_from_ttl,
)
from apps.dishacled.shacl.form import shacl_to_form_fields
from apps.dishacled.storage.dishacled_httpstore import DishacledHttpStorageManager


RDFC = "https://w3id.org/rdf-connect#"

# Two processors in one file, the shape of the real file-utils repository.
MULTI_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.
@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.

rdfc:GlobRead rdfc:jsImplementationOf rdfc:Processor;
    rdfc:file <./lib/index.js>;
    rdfc:class "GlobRead".

[ ] a sh:NodeShape;
    sh:targetClass rdfc:GlobRead;
    sh:property [
        sh:datatype xsd:string;
        sh:path rdfc:globPattern;
        sh:name "globPattern";
        sh:minCount 1;
        sh:maxCount 1;
    ], [
        sh:class rdfc:Writer;
        sh:path rdfc:writer;
        sh:name "writer";
        sh:minCount 1;
        sh:maxCount 1;
    ], [
        sh:datatype xsd:boolean;
        sh:path rdfc:closeOnEnd;
        sh:name "closeOnEnd";
        sh:maxCount 1;
    ].

rdfc:Envsub rdfc:jsImplementationOf rdfc:Processor;
    rdfc:file <./lib/index.js>;
    rdfc:class "Envsub".

[ ] a sh:NodeShape;
    sh:targetClass rdfc:Envsub;
    sh:property [
        sh:class rdfc:Reader;
        sh:path rdfc:input;
        sh:name "reader";
        sh:minCount 1;
        sh:maxCount 1;
    ], [
        sh:class rdfc:Writer;
        sh:path rdfc:output;
        sh:name "writer";
        sh:minCount 1;
        sh:maxCount 1;
    ].
"""

# A second file in the same repository, declaring a third processor. Its import
# must not travel with the components declared in the first file.
OTHER_FILE_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.
@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.

rdfc:HttpServer rdfc:jsImplementationOf rdfc:Processor;
    rdfc:class "HttpServer".

[ ] a sh:NodeShape;
    sh:targetClass rdfc:HttpServer;
    sh:property [
        sh:datatype xsd:integer;
        sh:path rdfc:port;
        sh:name "port";
        sh:minCount 1;
    ].
"""

PACKAGE_JSON = '{"name": "@rdfc/file-utils-processors-ts", "version": "1.0.1"}'


def _session(files: dict, package_json: str | None = PACKAGE_JSON):
    """A mocked GitHub session serving one repository with several TTL files."""
    repo = {
        "owner": {"login": "rdf-connect"},
        "name": "file-utils-processors-ts",
        "full_name": "rdf-connect/file-utils-processors-ts",
        "html_url": "https://github.com/rdf-connect/file-utils-processors-ts",
        "default_branch": "main",
        "language": "TypeScript",
        "description": "File utilities",
    }

    def _json_response(payload):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = payload
        return response

    def _contents(body):
        return _json_response(
            {
                "content": base64.b64encode(body.encode("utf-8")).decode("utf-8"),
                "encoding": "base64",
            }
        )

    missing = MagicMock()
    missing.status_code = 404

    def _get(url, **kwargs):
        if "/search/repositories" in url:
            return _json_response({"items": [repo], "total_count": 1})
        if "/git/trees/" in url:
            return _json_response({"tree": [{"path": p} for p in files]})
        if "/contents/package.json" in url:
            return _contents(package_json) if package_json else missing
        if "/contents/pyproject.toml" in url:
            return missing
        if "/contents/" in url:
            path = url.split("/contents/", 1)[1]
            return _contents(files[path]) if path in files else missing
        return _json_response(repo)

    session = MagicMock()
    session.get.side_effect = _get
    return session


def _store(files=None, package_json=PACKAGE_JSON):
    store = DishacledHttpStorageManager()
    store.session = _session(files or {"processors.ttl": MULTI_TTL}, package_json)
    return store


def _component(store, class_name="GlobRead"):
    return store.get_item_from_collection_by_id(
        "githubProcessors",
        f"rdf-connect--file-utils-processors-ts--{class_name}",
    )


def _pipeline(component_key, metadata):
    return {
        "_id": "pipeline-1",
        "type": "pipeline",
        "metadata": [{"key": "name", "value": "P"}],
        "relations": [
            {"key": component_key, "type": "hasProcessor", "metadata": metadata}
        ],
    }


class TestProcessorClassesFromTtl:
    def test_every_declared_processor_class_is_returned(self):
        assert processor_classes_from_ttl(MULTI_TTL) == [
            f"{RDFC}Envsub",
            f"{RDFC}GlobRead",
        ]

    def test_the_order_is_stable(self):
        # sorted, so two processes agree -- this is the whole point
        assert processor_classes_from_ttl(MULTI_TTL) == sorted(
            processor_classes_from_ttl(MULTI_TTL)
        )

    def test_a_file_without_a_processor_declares_none(self):
        assert processor_classes_from_ttl("@prefix rdfc: <x:> .") == []

    def test_unparseable_turtle_declares_none(self):
        assert processor_classes_from_ttl("not turtle @@@") == []

    def test_the_singular_helper_agrees_with_the_first_class(self):
        assert component_iri_from_ttl(MULTI_TTL) == f"{RDFC}Envsub"


class TestListingIsPerProcessorClass:
    def test_a_repository_with_two_processors_lists_two_components(self):
        results = _store().get_items_from_collection("githubProcessors")
        ids = [d["_id"] for d in results["results"] if d["type"] == "githubProcessor"]
        assert "rdf-connect--file-utils-processors-ts--GlobRead" in ids
        assert "rdf-connect--file-utils-processors-ts--Envsub" in ids

    def test_each_listed_component_is_named_after_its_processor(self):
        results = _store().get_items_from_collection("githubProcessors")
        names = {
            d["_id"]: next(
                m["value"] for m in d["metadata"] if m["key"] == "name"
            )
            for d in results["results"]
            if d["_id"].startswith("rdf-connect--")
        }
        assert names["rdf-connect--file-utils-processors-ts--GlobRead"] == "GlobRead"
        assert names["rdf-connect--file-utils-processors-ts--Envsub"] == "Envsub"

    def test_the_repository_it_came_from_is_still_recorded(self):
        results = _store().get_items_from_collection("githubProcessors")
        component = next(
            d
            for d in results["results"]
            if d["_id"].endswith("--GlobRead")
        )
        repository = next(
            m["value"] for m in component["metadata"] if m["key"] == "repository"
        )
        assert repository == "rdf-connect/file-utils-processors-ts"

    def test_a_repository_without_a_processor_is_still_listed(self):
        # Nothing about a repository that declares no processor should make it
        # vanish from the picker; it lists as itself, as it did before.
        results = _store(files={"notes.ttl": "@prefix ex: <x:> ."}).get_items_from_collection(
            "githubProcessors"
        )
        ids = [d["_id"] for d in results["results"]]
        assert "rdf-connect--file-utils-processors-ts" in ids


class TestComponentCarriesItsClass:
    def test_the_component_iri_is_the_class_the_id_names(self):
        assert _component(_store())["data"]["componentIri"] == f"{RDFC}GlobRead"

    def test_the_form_is_the_named_class_own_shape(self):
        fields = _component(_store())["data"]["formFields"]
        assert set(fields) == {"globPattern", "writer", "closeOnEnd"}

    def test_the_form_of_the_other_class_in_the_same_file_is_its_own(self):
        fields = _component(_store(), "Envsub")["data"]["formFields"]
        assert set(fields) == {"reader", "writer"}

    def test_the_properties_are_the_named_class_own(self):
        names = {p["name"] for p in _component(_store())["data"]["properties"]}
        assert names == {"globPattern", "writer", "closeOnEnd"}

    def test_the_ports_are_the_named_class_own(self):
        component = _component(_store())
        assert [(p["name"], p["direction"]) for p in component["data"]["ports"]] == [
            ("writer", "out")
        ]

    def test_the_same_id_resolves_to_the_same_class_every_time(self):
        seen = {_component(_store())["data"]["componentIri"] for _ in range(5)}
        assert seen == {f"{RDFC}GlobRead"}

    def test_an_unknown_class_in_a_known_repository_resolves_to_nothing(self):
        assert _component(_store(), "NoSuchProcessor") == {}

    def test_the_import_names_the_file_the_class_is_declared_in(self):
        store = _store(
            files={"processors.ttl": MULTI_TTL, "server.ttl": OTHER_FILE_TTL}
        )
        imports = _component(store)["data"]["deployment"]["imports"]
        assert imports == [
            "./node_modules/@rdfc/file-utils-processors-ts/processors.ttl"
        ]

    def test_a_legacy_repository_id_still_resolves(self):
        # Pipelines saved before this change hold `owner--repo` keys; they must
        # keep opening, on the first class the repository declares.
        component = _store().get_item_from_collection_by_id(
            "githubProcessors", "rdf-connect--file-utils-processors-ts"
        )
        assert component["data"]["componentIri"] == f"{RDFC}Envsub"


class TestFormAndExportAgree:
    """The form the user fills and the shape the export writes are one shape."""

    def test_the_form_and_the_export_pick_the_same_class(self):
        component = _component(_store())
        shape = _ShapeIndex.from_ttl(
            component["data"]["rawTtl"],
            target_class=component["data"]["componentIri"],
        )
        assert set(component["data"]["formFields"]) == set(shape.properties)

    def test_the_export_writes_the_class_the_component_names(self):
        store = _store()
        component = _component(store)
        ttl = PipelineTtlSerializer(base_uri="http://x/p1/").serialize(
            _pipeline(
                component["_id"],
                [
                    {"key": "globPattern", "value": "./resources/mapping.rml.ttl"},
                    {"key": "closeOnEnd", "value": "true"},
                ],
            ),
            {component["_id"]: component},
        )
        assert "rdfc:GlobRead" in ttl
        assert "rdfc:Envsub" not in ttl

    def test_the_config_the_user_typed_survives_the_export(self):
        # The bug this fixes: with the wrong shape selected, every value whose
        # predicate that shape does not know was dropped without a word.
        store = _store()
        component = _component(store)
        ttl = PipelineTtlSerializer(base_uri="http://x/p1/").serialize(
            _pipeline(
                component["_id"],
                [
                    {"key": "globPattern", "value": "./resources/mapping.rml.ttl"},
                    {"key": "closeOnEnd", "value": "true"},
                ],
            ),
            {component["_id"]: component},
        )
        assert '"./resources/mapping.rml.ttl"' in ttl
        assert "rdfc:globPattern" in ttl

    def test_the_definition_export_specializes_the_named_class(self):
        store = _store()
        component = _component(store)
        ttl = PipelineDefinitionSerializer(base_uri="http://x/p1/").serialize(
            _pipeline(
                component["_id"],
                [{"key": "globPattern", "value": "./m.ttl"}],
            ),
            {component["_id"]: component},
        )
        assert "prov:specializationOf rdfc:GlobRead" in ttl.replace("\n", " ")
        assert "rdfc:globPattern" in ttl

    def test_two_classes_from_one_repository_are_two_steps(self):
        store = _store()
        reader, sub = _component(store), _component(store, "Envsub")
        pipeline = {
            "_id": "pipeline-1",
            "type": "pipeline",
            "metadata": [],
            "relations": [
                {
                    "key": reader["_id"],
                    "type": "hasProcessor",
                    "metadata": [{"key": "globPattern", "value": "./m.ttl"}],
                },
                {
                    "key": sub["_id"],
                    "type": "hasProcessor",
                    "metadata": [
                        {
                            "key": "connections.reader.from",
                            "value": f"{reader['_id']}|writer",
                        }
                    ],
                },
            ],
        }
        ttl = PipelineTtlSerializer(base_uri="http://x/p1/").serialize(
            pipeline, {reader["_id"]: reader, sub["_id"]: sub}
        )
        assert "rdfc:GlobRead" in ttl and "rdfc:Envsub" in ttl
        # and the connection between them binds one channel to both ends: the
        # producing step and its port name it (`channel_name_between`), so it
        # appears on the writer, on the reader and in its own declaration
        assert ttl.count("globread-writer-channel") >= 3


class TestExplicitTargetClass:
    def test_the_shape_index_takes_the_class_it_is_given(self):
        shape = _ShapeIndex.from_ttl(MULTI_TTL, target_class=f"{RDFC}Envsub")
        assert str(shape.target_class) == f"{RDFC}Envsub"
        assert set(shape.properties) == {"reader", "writer"}

    def test_an_absent_class_falls_back_rather_than_failing(self):
        shape = _ShapeIndex.from_ttl(MULTI_TTL, target_class=f"{RDFC}Missing")
        assert shape is not None

    def test_the_fallback_is_stable(self):
        assert (
            str(_ShapeIndex.from_ttl(MULTI_TTL).target_class) == f"{RDFC}Envsub"
        )

    def test_the_form_takes_the_class_it_is_given(self):
        fields = shacl_to_form_fields(MULTI_TTL, target_class=f"{RDFC}GlobRead")
        assert set(fields) == {"globPattern", "writer", "closeOnEnd"}

    def test_the_form_fallback_is_stable(self):
        assert set(shacl_to_form_fields(MULTI_TTL)) == {"reader", "writer"}


class TestPortsOfAPerClassComponent:
    def test_a_reader_property_is_an_input_port(self):
        component = _component(_store(), "Envsub")
        ports = [(p.name, p.direction) for p in ports_for_component(component)]
        assert ("reader", "in") in ports
        assert ("writer", "out") in ports


# A processor described by a shape alone: no `rdfc:*ImplementationOf` triple,
# which is what older processor files look like.
SHAPE_ONLY_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.
@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.

[ ] a sh:NodeShape;
    sh:targetClass rdfc:LdesClient;
    sh:property [
        sh:datatype xsd:string;
        sh:path rdfc:url;
        sh:name "url";
        sh:minCount 1;
    ].
"""


class TestAShapeIsEnoughToBeAComponent:
    """A file that declares no implementation still describes a component.

    Requiring the implementation triple would drop the component and its config
    form together, so the shape's `sh:targetClass` is read as the weaker
    evidence of the same thing.
    """

    def _store(self):
        return _store(files={"processor.ttl": SHAPE_ONLY_TTL})

    def test_the_component_is_the_class_the_shape_targets(self):
        component = _component(self._store(), "LdesClient")
        assert component["data"]["componentIri"] == f"{RDFC}LdesClient"
        assert set(component["data"]["formFields"]) == {"url"}

    def test_it_is_listed(self):
        results = self._store().get_items_from_collection("githubProcessors")
        assert (
            "rdf-connect--file-utils-processors-ts--LdesClient"
            in [d["_id"] for d in results["results"]]
        )

    def test_no_import_is_synthesised_for_it(self):
        # nothing said this file defines a processor, so nothing here should
        # claim the runner can load it from that path
        component = _component(self._store(), "LdesClient")
        assert component["data"]["deployment"]["imports"] == []


class TestListingStillPages:
    def test_the_count_stays_a_repository_count(self):
        # It is what decides whether there is a next page, and a page is a page
        # of GitHub search results. Counting the components on this page would
        # claim a total that says nothing about the pages after it.
        from apps.dishacled.storage.local_component_source import (
            LocalComponentSource,
        )

        results = _store().get_items_from_collection("githubProcessors")
        assert results["count"] == 1 + len(LocalComponentSource().list_documents())
        # ...while the page itself carries both processors
        assert len([d for d in results["results"] if "--file-utils" in d["_id"]]) == 2
