"""A catalog entry may carry only deployment coordinates.

The contract catalog is where curated facts about a component live, and
`owl:imports` / `spdx:Package` are facts of exactly that kind: a jvm or py
processor has no npm manifest to read a path from, so nothing else can supply
them and the exported pipeline names a class the runner cannot resolve.

Such an entry was silently ignored -- `_discover` registered a subject only if
it carried a shape in a role it understood, so a deployment-only entry was read
as "a qualifiedRelation carrying no role", skipped, and the imports never
reached the component. Which looks, from the outside, exactly like the file was
never edited.
"""

import pathlib
import tempfile

import pytest

from apps.dishacled.shacl import contracts as contracts_module
from apps.dishacled.shacl.contracts import ContractCatalog


PREFIXES = """\
@prefix dcat: <http://www.w3.org/ns/dcat#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix rdfc: <https://w3id.org/rdf-connect#> .
@prefix spdx: <http://spdx.org/rdf/terms#> .
@prefix tcs: <https://w3id.org/toolchain#> .
"""

DEPLOYMENT_ONLY = (
    PREFIXES
    + """
rdfc:RmlMapper a tcs:PipelineComponent, dcat:DataService ;
  rdfs:label "RML mapper" ;
  dcat:landingPage <https://github.com/rdf-connect/rml-processor-jvm> ;
  owl:imports <./build/plugins/rml-processor-jvm-master-SNAPSHOT-all.jar> .
"""
)

NOTHING_TO_SAY = (
    PREFIXES
    + """
rdfc:Bare a tcs:PipelineComponent ;
  rdfs:label "Bare" .
"""
)

UNTYPED = (
    PREFIXES
    + """
<https://example.org/not-a-component>
  owl:imports <./somewhere/else.ttl> .
"""
)


def catalog_of(ttl):
    return ContractCatalog.from_ttl(ttl)


class TestADeploymentOnlyEntryIsAContract:
    def test_it_is_discovered(self):
        contract = catalog_of(DEPLOYMENT_ONLY).get(
            "https://w3id.org/rdf-connect#RmlMapper"
        )
        assert contract is not None

    def test_it_carries_the_import(self):
        contract = catalog_of(DEPLOYMENT_ONLY).get(
            "https://w3id.org/rdf-connect#RmlMapper"
        )
        assert contract.to_data()["deployment"]["imports"] == [
            "./build/plugins/rml-processor-jvm-master-SNAPSHOT-all.jar"
        ]

    def test_it_declares_no_shapes(self):
        contract = catalog_of(DEPLOYMENT_ONLY).get(
            "https://w3id.org/rdf-connect#RmlMapper"
        )
        data = contract.to_data()
        assert data.get("configShape") is None
        assert data.get("inputShape") is None

    def test_it_is_an_overlay_not_a_component_of_its_own(self):
        # `dcat:landingPage` says the component is discovered on GitHub and
        # this entry only adds to it; without one it would be listed twice
        contract = catalog_of(DEPLOYMENT_ONLY).get(
            "https://w3id.org/rdf-connect#RmlMapper"
        )
        assert contract.landing_page


class TestNothingElseBecomesAComponent:
    def test_an_entry_with_neither_shapes_nor_coordinates_is_ignored(self):
        assert catalog_of(NOTHING_TO_SAY).all() == []

    def test_a_subject_that_is_not_a_component_is_ignored(self):
        # a stray owl:imports somewhere in the file must not invent a component
        assert catalog_of(UNTYPED).all() == []


class TestTheShippedCatalogIsUnchanged:
    def test_the_same_components_are_still_declared(self):
        catalog = ContractCatalog.default()
        iris = {contract.iri for contract in catalog.all()}
        assert "https://dishacled.github.io/demo#ThresholdMonitorCm" in iris
        assert "https://dishacled.github.io/demo#AlertStore" in iris


class TestItReachesTheComponent:
    def test_a_curated_import_overrides_an_empty_repository_deployment(
        self, monkeypatch, tmp_path
    ):
        from apps.dishacled.storage.dishacled_httpstore import (
            DishacledHttpStorageManager,
        )

        shipped = contracts_module.DEFAULT_CONTRACTS_PATH.read_text()
        patched = tmp_path / "contracts.ttl"
        patched.write_text(
            shipped
            + """
rdfc:RmlMapper a tcs:PipelineComponent, dcat:DataService ;
  rdfs:label "RML mapper" ;
  dcat:landingPage <https://github.com/rdf-connect/rml-processor-jvm> ;
  owl:imports <./build/plugins/rml-processor-jvm-master-SNAPSHOT-all.jar> .
"""
        )
        monkeypatch.setattr(contracts_module, "DEFAULT_CONTRACTS_PATH", patched)
        contracts_module._default_catalog.cache_clear()
        try:
            store = DishacledHttpStorageManager()
            overlay = store._contract_overlay("https://w3id.org/rdf-connect#RmlMapper")
            assert overlay["deployment"]["imports"] == [
                "./build/plugins/rml-processor-jvm-master-SNAPSHOT-all.jar"
            ]
        finally:
            contracts_module._default_catalog.cache_clear()
