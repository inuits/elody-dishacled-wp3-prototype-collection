"""Tests for the component contract catalog.

Covers the DiSHACLed discovery vocabulary for attaching shapes to components:
`dcat:qualifiedRelation` -> `dcat:hadRole` -> `dcterms:relation` (spec 2.2),
with `dcterms:conformsTo` tolerated in the same position.

Reference: https://dishacled.github.io/discovery-specification/
"""

from rdflib import Graph

from apps.dishacled.shacl.contracts import (
    ContractCatalog,
    DEFAULT_CONTRACTS_PATH,
    LOCAL_ID_PREFIX,
    component_iri_from_ttl,
    local_id_for,
)


PREFIXES = """\
@prefix sh: <http://www.w3.org/ns/shacl#>.
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#>.
@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.
@prefix dcat: <http://www.w3.org/ns/dcat#>.
@prefix dcterms: <http://purl.org/dc/terms/>.
@prefix tcs: <https://w3id.org/toolchain#>.
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix demo: <https://dishacled.github.io/demo#>.
"""

MEASUREMENT_SHAPES = """\
demo:CmShape a sh:NodeShape;
  rdfs:label "Measurements in cm";
  sh:targetClass demo:Measurement;
  sh:property [
    sh:path demo:unit; sh:name "unit"; sh:datatype xsd:string; sh:in ("cm");
    sh:minCount 1;
  ], [
    sh:path demo:value; sh:name "value"; sh:datatype xsd:decimal; sh:minCount 1;
  ].

demo:MmShape a sh:NodeShape;
  rdfs:label "Measurements in mm";
  sh:targetClass demo:Measurement;
  sh:property [
    sh:path demo:unit; sh:name "unit"; sh:datatype xsd:string; sh:in ("mm");
    sh:minCount 1;
  ].
"""

# canonical spelling: `a dcat:Relationship`, dcterms:relation, inline config
CONTRACT_WITH_ALL_ROLES = PREFIXES + MEASUREMENT_SHAPES + """
demo:MonitorCm a tcs:PipelineComponent, dcat:DataService;
  rdfs:label "Monitor (cm)";
  rdfs:comment "Watches centimetre readings.";
  rdfc:jsImplementationOf rdfc:Processor;
  rdfc:class "MonitorCm";
  dcat:qualifiedRelation [
    a dcat:Relationship;
    dcat:hadRole tcs:configShape;
    dcterms:relation [
      a sh:NodeShape;
      sh:targetClass demo:MonitorCm;
      sh:property [
        sh:path demo:threshold; sh:name "threshold"; sh:datatype xsd:decimal;
        sh:minCount 1;
      ]
    ]
  ], [
    a dcat:Relationship;
    dcat:hadRole tcs:inputShape;
    dcterms:relation demo:CmShape;
  ], [
    a dcat:Relationship;
    dcat:hadRole tcs:outputShape;
    dcterms:relation demo:CmShape;
  ].
"""

# the task brief's spelling: conformsTo inside the relationship, and no
# `a dcat:Relationship` type on the relation node
CONTRACT_WITH_CONFORMS_TO = PREFIXES + MEASUREMENT_SHAPES + """
demo:MonitorMm a tcs:PipelineComponent, dcat:DataService;
  rdfs:label "Monitor (mm)";
  dcat:qualifiedRelation [
    dcat:hadRole tcs:inputShape;
    dcterms:conformsTo demo:MmShape;
  ], [
    dcat:hadRole tcs:outputShape;
    dcterms:conformsTo demo:MmShape;
  ].
"""

# the spec's own example role names, in place of the tcs: ones
CONTRACT_WITH_SPEC_ROLE_NAMES = PREFIXES + MEASUREMENT_SHAPES + """
demo:SpecNamed a dcat:DataService;
  rdfs:label "Spec-named roles";
  dcat:qualifiedRelation [
    a dcat:Relationship;
    dcat:hadRole tcs:InputDataShape;
    dcterms:relation demo:CmShape;
  ], [
    a dcat:Relationship;
    dcat:hadRole tcs:OutputDataShape;
    dcterms:relation demo:MmShape;
  ].
"""

CONTRACT_WITH_INLINE_INPUT = PREFIXES + """
demo:InlineIn a dcat:DataService;
  rdfs:label "Inline input";
  dcat:qualifiedRelation [
    a dcat:Relationship;
    dcat:hadRole tcs:inputShape;
    dcterms:relation [
      a sh:NodeShape;
      sh:targetClass demo:SensorReading;
      sh:property [
        sh:path demo:reading; sh:name "reading"; sh:datatype xsd:decimal;
        sh:minCount 1;
      ]
    ]
  ].
"""

# an input shape whose property points at another named shape via sh:node --
# the referenced shape has to be pulled into the extracted sub-graph
CONTRACT_WITH_SH_NODE = PREFIXES + """
demo:EnvelopeShape a sh:NodeShape;
  sh:targetClass demo:Envelope;
  sh:property [
    sh:path demo:payload; sh:name "payload"; sh:node demo:PayloadShape;
    sh:minCount 1;
  ].

demo:PayloadShape a sh:NodeShape;
  sh:targetClass demo:Payload;
  sh:property [
    sh:path demo:body; sh:name "body"; sh:datatype xsd:string;
  ].

demo:Enveloped a dcat:DataService;
  dcat:qualifiedRelation [
    dcat:hadRole tcs:inputShape;
    dcterms:relation demo:EnvelopeShape;
  ].
"""

DATASET_OUTPUT_ONLY = PREFIXES + MEASUREMENT_SHAPES + """
demo:FeedCm a dcat:Dataset;
  rdfs:label "Feed (cm)";
  dcat:qualifiedRelation [
    a dcat:Relationship;
    dcat:hadRole tcs:outputShape;
    dcterms:relation demo:CmShape;
  ].
"""

# a plain processor TTL, no dcat vocabulary at all
COMPONENT_WITHOUT_CONTRACT = PREFIXES + """
rdfc:HttpFetch rdfc:jsImplementationOf rdfc:Processor;
  rdfs:label "http fetch".

[ ] a sh:NodeShape;
  sh:targetClass rdfc:HttpFetch;
  sh:property [ sh:path rdfc:url; sh:name "url"; sh:datatype xsd:string; ].
"""

CM = "https://dishacled.github.io/demo#CmShape"
MM = "https://dishacled.github.io/demo#MmShape"
MONITOR_CM = "https://dishacled.github.io/demo#MonitorCm"


class TestRoleParsing:
    def test_config_input_and_output_are_parsed(self):
        c = ContractCatalog.from_ttl(CONTRACT_WITH_ALL_ROLES).get(MONITOR_CM)
        assert c.config_shape is not None
        assert c.input_shape is not None
        assert c.output_shape is not None

    def test_roles_are_independent(self):
        c = ContractCatalog.from_ttl(CONTRACT_WITH_ALL_ROLES).get(MONITOR_CM)
        # the config shape is inline and unrelated to the interface shapes
        assert c.config_shape.iri is None
        assert c.input_shape.iri == CM
        assert {p.name for p in c.config_shape.properties} == {"threshold"}

    def test_shape_ref_carries_its_role(self):
        c = ContractCatalog.from_ttl(CONTRACT_WITH_ALL_ROLES).get(MONITOR_CM)
        assert c.input_shape.role == "input"
        assert c.output_shape.role == "output"
        assert c.config_shape.role == "config"

    def test_input_shape_properties_are_parsed(self):
        c = ContractCatalog.from_ttl(CONTRACT_WITH_ALL_ROLES).get(MONITOR_CM)
        props = {p.name: p for p in c.input_shape.properties}
        assert set(props) == {"unit", "value"}
        assert props["unit"].in_values == ["cm"]
        assert props["value"].is_required is True

    def test_shape_ttl_is_self_contained_and_reparseable(self):
        c = ContractCatalog.from_ttl(CONTRACT_WITH_ALL_ROLES).get(MONITOR_CM)
        # B3 needs to hand this straight to a SHACL validator
        assert len(Graph().parse(data=c.input_shape.ttl, format="turtle")) > 0

    def test_shape_label_is_carried(self):
        c = ContractCatalog.from_ttl(CONTRACT_WITH_ALL_ROLES).get(MONITOR_CM)
        assert c.input_shape.label == "Measurements in cm"

    def test_component_label_and_comment_are_carried(self):
        c = ContractCatalog.from_ttl(CONTRACT_WITH_ALL_ROLES).get(MONITOR_CM)
        assert c.label == "Monitor (cm)"
        assert c.comment == "Watches centimetre readings."


class TestLinkPredicateTolerance:
    def test_dcterms_relation_is_accepted(self):
        c = ContractCatalog.from_ttl(CONTRACT_WITH_ALL_ROLES).get(MONITOR_CM)
        assert c.input_shape.iri == CM

    def test_dcterms_conforms_to_is_accepted(self):
        catalog = ContractCatalog.from_ttl(CONTRACT_WITH_CONFORMS_TO)
        c = catalog.get("https://dishacled.github.io/demo#MonitorMm")
        assert c.input_shape.iri == MM
        assert c.output_shape.iri == MM

    def test_missing_dcat_relationship_type_is_accepted(self):
        # the reference catalog omits `a dcat:Relationship`
        catalog = ContractCatalog.from_ttl(CONTRACT_WITH_CONFORMS_TO)
        assert catalog.get("https://dishacled.github.io/demo#MonitorMm")

    def test_spec_example_role_names_are_accepted(self):
        catalog = ContractCatalog.from_ttl(CONTRACT_WITH_SPEC_ROLE_NAMES)
        c = catalog.get("https://dishacled.github.io/demo#SpecNamed")
        assert c.input_shape.iri == CM
        assert c.output_shape.iri == MM


class TestShapeForms:
    def test_inline_blank_node_shape_has_no_iri_but_has_ttl(self):
        catalog = ContractCatalog.from_ttl(CONTRACT_WITH_INLINE_INPUT)
        shape = catalog.get("https://dishacled.github.io/demo#InlineIn").input_shape
        assert shape.iri is None
        assert {p.name for p in shape.properties} == {"reading"}

    def test_iri_reference_resolves_shape_defined_elsewhere_in_graph(self):
        c = ContractCatalog.from_ttl(CONTRACT_WITH_ALL_ROLES).get(MONITOR_CM)
        # demo:CmShape is declared at the top of the file, not inline
        assert "unit" in {p.name for p in c.input_shape.properties}

    def test_referenced_named_shape_is_pulled_into_the_subgraph(self):
        catalog = ContractCatalog.from_ttl(CONTRACT_WITH_SH_NODE)
        shape = catalog.get("https://dishacled.github.io/demo#Enveloped").input_shape
        g = Graph().parse(data=shape.ttl, format="turtle")
        subjects = {str(s) for s in g.subjects()}
        assert "https://dishacled.github.io/demo#PayloadShape" in subjects

    def test_sh_in_list_survives_subgraph_extraction(self):
        # an RDF list is a chain of blank nodes; naive extraction loses it
        c = ContractCatalog.from_ttl(CONTRACT_WITH_ALL_ROLES).get(MONITOR_CM)
        unit = next(p for p in c.input_shape.properties if p.name == "unit")
        assert unit.in_values == ["cm"]

    def test_shape_to_dict_exposes_iri_ttl_and_properties(self):
        c = ContractCatalog.from_ttl(CONTRACT_WITH_ALL_ROLES).get(MONITOR_CM)
        d = c.input_shape.to_dict()
        assert d["iri"] == CM
        assert d["label"] == "Measurements in cm"
        assert "sh:NodeShape" in d["ttl"] or "NodeShape" in d["ttl"]
        # same property dict shape the PWA already renders
        unit = next(p for p in d["properties"] if p["name"] == "unit")
        assert set(unit) == {
            "name",
            "inputFieldType",
            "isRequired",
            "inValues",
            "classRef",
        }


class TestDatasets:
    def test_dataset_has_output_shape_only(self):
        catalog = ContractCatalog.from_ttl(DATASET_OUTPUT_ONLY)
        d = catalog.get("https://dishacled.github.io/demo#FeedCm")
        assert d.output_shape.iri == CM
        assert d.input_shape is None
        assert d.config_shape is None

    def test_dataset_kind_is_dataset(self):
        catalog = ContractCatalog.from_ttl(DATASET_OUTPUT_ONLY)
        assert catalog.get("https://dishacled.github.io/demo#FeedCm").kind == "dataset"

    def test_service_kind_is_component(self):
        c = ContractCatalog.from_ttl(CONTRACT_WITH_ALL_ROLES).get(MONITOR_CM)
        assert c.kind == "component"


class TestCatalog:
    def test_from_ttl_loads_multiple_components(self):
        ttl = CONTRACT_WITH_ALL_ROLES + DATASET_OUTPUT_ONLY.replace(PREFIXES, "")
        catalog = ContractCatalog.from_ttl(ttl)
        assert len(catalog.all()) == 2

    def test_all_returns_stable_order(self):
        ttl = CONTRACT_WITH_ALL_ROLES + DATASET_OUTPUT_ONLY.replace(PREFIXES, "")
        first = [c.iri for c in ContractCatalog.from_ttl(ttl).all()]
        second = [c.iri for c in ContractCatalog.from_ttl(ttl).all()]
        assert first == second == sorted(first)

    def test_get_unknown_iri_returns_none(self):
        catalog = ContractCatalog.from_ttl(CONTRACT_WITH_ALL_ROLES)
        assert catalog.get("https://dishacled.github.io/demo#Nope") is None

    def test_get_by_local_id(self):
        catalog = ContractCatalog.from_ttl(CONTRACT_WITH_ALL_ROLES)
        assert catalog.get_by_local_id("local--monitor-cm").iri == MONITOR_CM

    def test_local_id_is_kebab_cased_with_prefix(self):
        assert local_id_for(MONITOR_CM) == "local--monitor-cm"
        assert local_id_for(
            "https://dishacled.github.io/demo#ThresholdMonitorCm"
        ) == "local--threshold-monitor-cm"
        assert LOCAL_ID_PREFIX == "local--"

    def test_component_without_contract_is_not_discovered(self):
        assert ContractCatalog.from_ttl(COMPONENT_WITHOUT_CONTRACT).all() == []

    def test_empty_ttl_yields_empty_catalog(self):
        assert ContractCatalog.from_ttl("").all() == []

    def test_invalid_ttl_yields_empty_catalog(self):
        assert ContractCatalog.from_ttl("not turtle @@@").all() == []

    def test_missing_file_yields_empty_catalog(self):
        assert ContractCatalog.from_file("/nonexistent/contracts.ttl").all() == []


class TestComponentIriFromTtl:
    def test_reads_the_implementation_subject(self):
        assert (
            component_iri_from_ttl(COMPONENT_WITHOUT_CONTRACT)
            == "https://w3id.org/rdf-connect#HttpFetch"
        )

    def test_matches_any_runtime_implementation_predicate(self):
        ttl = PREFIXES + "rdfc:LogPy rdfc:pyImplementationOf rdfc:Processor."
        assert component_iri_from_ttl(ttl) == "https://w3id.org/rdf-connect#LogPy"

    def test_returns_none_without_a_processor(self):
        assert component_iri_from_ttl(PREFIXES) is None

    def test_returns_none_for_invalid_ttl(self):
        assert component_iri_from_ttl("not turtle @@@") is None


class TestBundledContractsFixture:
    """The real interim catalog shipped under api/, loaded the way it is at runtime."""

    CM_SHAPE = "https://dishacled.github.io/demo#MeasurementsInCmShape"
    MM_SHAPE = "https://dishacled.github.io/demo#MeasurementsInMmShape"

    def catalog(self):
        return ContractCatalog.from_file(DEFAULT_CONTRACTS_PATH)

    def test_file_exists_and_parses(self):
        assert DEFAULT_CONTRACTS_PATH.exists()
        assert len(self.catalog().all()) > 0

    def test_four_components_and_one_dataset(self):
        entries = self.catalog().all()
        assert len([c for c in entries if c.kind == "component"]) == 4
        assert len([c for c in entries if c.kind == "dataset"]) == 1

    def test_every_component_has_config_input_and_output(self):
        for c in self.catalog().all():
            if c.kind != "component":
                continue
            assert c.config_shape is not None, c.iri
            assert c.input_shape is not None, c.iri
            assert c.output_shape is not None, c.iri

    def test_dataset_declares_output_only(self):
        dataset = next(c for c in self.catalog().all() if c.kind == "dataset")
        assert dataset.output_shape is not None
        assert dataset.input_shape is None
        assert dataset.config_shape is None

    def test_cm_and_mm_shapes_are_distinct(self):
        assert self.CM_SHAPE != self.MM_SHAPE
        catalog = self.catalog()
        cm = catalog.get("https://dishacled.github.io/demo#ThresholdMonitorCm")
        mm = catalog.get("https://dishacled.github.io/demo#ThresholdMonitorMm")
        assert cm.input_shape.iri == self.CM_SHAPE
        assert mm.input_shape.iri == self.MM_SHAPE

    def test_matching_chain_cm_to_cm(self):
        # http-poller-cm -> threshold-monitor-cm is the compatible demo chain
        catalog = self.catalog()
        poller = catalog.get("https://dishacled.github.io/demo#HttpPollerCm")
        monitor = catalog.get("https://dishacled.github.io/demo#ThresholdMonitorCm")
        assert poller.output_shape.iri == monitor.input_shape.iri

    def test_mismatching_chain_mm_to_cm(self):
        # http-poller-mm -> threshold-monitor-cm is the mismatch B3 demonstrates
        catalog = self.catalog()
        poller = catalog.get("https://dishacled.github.io/demo#HttpPollerMm")
        monitor = catalog.get("https://dishacled.github.io/demo#ThresholdMonitorCm")
        assert poller.output_shape.iri != monitor.input_shape.iri

    def test_unit_constraints_differ_between_cm_and_mm(self):
        catalog = self.catalog()
        cm = catalog.get("https://dishacled.github.io/demo#ThresholdMonitorCm")
        mm = catalog.get("https://dishacled.github.io/demo#ThresholdMonitorMm")
        cm_unit = next(p for p in cm.output_shape.properties if p.name == "unit")
        mm_unit = next(p for p in mm.output_shape.properties if p.name == "unit")
        assert cm_unit.in_values == ["cm"]
        assert mm_unit.in_values == ["mm"]

    def test_every_component_declares_a_processor_class_iri(self):
        # the join key against a GitHub-discovered processor
        for c in self.catalog().all():
            if c.kind != "component":
                continue
            assert component_iri_from_ttl(c.to_raw_ttl()) == c.iri

    def test_dataset_raw_ttl_does_not_claim_to_be_a_processor(self):
        dataset = next(c for c in self.catalog().all() if c.kind == "dataset")
        assert component_iri_from_ttl(dataset.to_raw_ttl()) is None

    def test_component_raw_ttl_yields_config_form_fields(self):
        from apps.dishacled.shacl.form import shacl_to_form_fields

        catalog = self.catalog()
        monitor = catalog.get("https://dishacled.github.io/demo#ThresholdMonitorCm")
        fields = shacl_to_form_fields(monitor.to_raw_ttl())
        assert fields["threshold"]["inputField"]["type"] == "number"
        assert fields["direction"]["inputField"]["type"] == "dropdown"
        # rdfc:Writer-typed config property becomes a channel dropdown
        assert fields["output"]["inputField"]["channelField"] is True

    def test_local_ids_are_the_demo_component_names(self):
        ids = {c.local_id for c in self.catalog().all()}
        assert {
            "local--threshold-monitor-cm",
            "local--threshold-monitor-mm",
            "local--http-poller-cm",
            "local--http-poller-mm",
        } <= ids

    def test_to_data_exposes_the_entity_payload(self):
        catalog = self.catalog()
        data = catalog.get(
            "https://dishacled.github.io/demo#ThresholdMonitorCm"
        ).to_data()
        assert data["componentIri"] == (
            "https://dishacled.github.io/demo#ThresholdMonitorCm"
        )
        assert data["componentKind"] == "component"
        assert data["inputShape"]["iri"] == self.CM_SHAPE
        assert data["outputShape"]["iri"] == self.CM_SHAPE
        assert data["configShape"]["properties"]

    def test_dataset_to_data_has_null_input_and_config(self):
        dataset = next(c for c in self.catalog().all() if c.kind == "dataset")
        data = dataset.to_data()
        assert data["inputShape"] is None
        assert data["configShape"] is None
        assert data["outputShape"]["iri"] == self.CM_SHAPE


class TestDeploymentCoordinates:
    """Where a component's implementation is fetched from, and what runs it.

    The toolchain pipeline generator needs more than shapes to build a project:
    it has to know which package to install (`spdx:Package`) and which file to
    import so the runner can find the processor (`owl:imports`). Those live on
    the component in the catalog because they are not derivable from anything
    else we hold.
    """

    DEPLOYABLE = (
        PREFIXES
        + """
demo:Poller a tcs:PipelineComponent;
  rdfs:label "Poller";
  owl:imports <./node_modules/@acme/processors/processors.ttl>;
  dcterms:requires [
    a spdx:Package;
    spdx:name "@acme/processors";
    spdx:versionInfo "^1.2.3";
    spdx:suppliedBy <http://example.org/example/npm>;
  ];
  dcat:qualifiedRelation [
    dcat:hadRole tcs:outputShape; dcterms:relation demo:CmShape
  ].
"""
        + MEASUREMENT_SHAPES
    )

    def contract(self):
        extra = "@prefix owl: <http://www.w3.org/2002/07/owl#>.\n@prefix spdx: <http://spdx.org/rdf/terms#>.\n"
        return ContractCatalog.from_ttl(extra + self.DEPLOYABLE).get(
            "https://dishacled.github.io/demo#Poller"
        )

    def test_imports_are_read(self):
        assert self.contract().deployment.imports == (
            "./node_modules/@acme/processors/processors.ttl",
        )

    def test_package_name_and_version_are_read(self):
        package = self.contract().deployment.packages[0]
        assert package.name == "@acme/processors"
        assert package.version == "^1.2.3"

    def test_package_supplier_is_read(self):
        package = self.contract().deployment.packages[0]
        assert package.supplier == "http://example.org/example/npm"

    def test_a_component_without_coordinates_has_an_empty_deployment(self):
        catalog = ContractCatalog.from_ttl(CONTRACT_WITH_ALL_ROLES)
        contract = catalog.get(MONITOR_CM)
        assert contract.deployment.imports == ()
        assert contract.deployment.packages == ()

    def test_deployment_travels_on_the_entity_data(self):
        data = self.contract().to_data()
        assert data["deployment"]["imports"] == [
            "./node_modules/@acme/processors/processors.ttl"
        ]
        assert data["deployment"]["packages"][0]["name"] == "@acme/processors"


class TestBundledDeploymentCoordinates:
    """The shipped demo components must be installable by the generator."""

    def components(self):
        return [
            c
            for c in ContractCatalog.from_file(DEFAULT_CONTRACTS_PATH).all()
            if c.kind == "component"
        ]

    def test_every_component_declares_an_import(self):
        for contract in self.components():
            assert contract.deployment.imports, contract.iri

    def test_every_component_declares_a_package(self):
        for contract in self.components():
            assert contract.deployment.packages, contract.iri

    def test_packages_are_routed_to_a_manager_the_generator_knows(self):
        # RdfcDockerFileCompiler routes on spdx:suppliedBy; anything other than
        # :pip / :npm is silently dropped from both manifest files.
        known = {
            "http://example.org/example/npm",
            "http://example.org/example/pip",
        }
        for contract in self.components():
            for package in contract.deployment.packages:
                assert package.supplier in known, contract.iri

    def test_a_dataset_needs_no_deployment_coordinates(self):
        dataset = next(
            c
            for c in ContractCatalog.from_file(DEFAULT_CONTRACTS_PATH).all()
            if c.kind == "dataset"
        )
        assert dataset.deployment.packages == ()
