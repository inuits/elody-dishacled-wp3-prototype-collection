"""Components that live in the contract catalog rather than in a repository.

Processors are normally discovered from GitHub, but the interim catalog also
declares components that have no repository yet -- the per-unit demo variants.
They are served as ordinary `githubProcessor` documents, in exactly the
envelope GithubSerializer produces, so listing, detail, the config form and the
pipeline export all keep working without a new entity type.

Two things keep them distinguishable from real repositories: the `local--` id
prefix and a `source: contracts` metadata entry.
"""

from apps.dishacled.pipeline.connections import ports_for_component
from apps.dishacled.shacl.contracts import (
    ComponentContract,
    ContractCatalog,
    LOCAL_ID_PREFIX,
)
from apps.dishacled.shacl.form import shacl_to_form_fields


class LocalComponentSource:
    def __init__(self, catalog: ContractCatalog | None = None):
        self._catalog = catalog

    @property
    def catalog(self) -> ContractCatalog:
        # resolved lazily so a broken catalog cannot break construction of the
        # storage manager itself
        if self._catalog is None:
            self._catalog = ContractCatalog.default()
        return self._catalog

    def matches(self, id: str) -> bool:
        return bool(id) and str(id).startswith(LOCAL_ID_PREFIX)

    def get_document(self, id: str) -> dict | None:
        contract = self.catalog.get_by_local_id(id)
        return self._to_document(contract) if contract else None

    def list_documents(self, query: str = "") -> list[dict]:
        contracts = self.catalog.all()
        if query:
            needle = query.lower()
            contracts = [
                c
                for c in contracts
                if needle in (c.label or "").lower()
                or needle in (c.comment or "").lower()
                or needle in c.local_id
            ]
        return [self._to_document(c) for c in contracts]

    def _to_document(self, contract: ComponentContract) -> dict:
        raw_ttl = contract.to_raw_ttl()
        try:
            form_fields = shacl_to_form_fields(raw_ttl)
        except Exception:
            form_fields = {}

        config_properties = (
            contract.config_shape.to_dict()["properties"]
            if contract.config_shape
            else []
        )

        document = {
            "_id": contract.local_id,
            "identifiers": [contract.local_id, contract.iri],
            "type": "githubProcessor",
            "metadata": [
                {"key": "name", "value": contract.label or contract.local_id},
                {"key": "description", "value": contract.comment or ""},
                {"key": "url", "value": contract.iri},
                {"key": "runtime", "value": "ts"},
                {"key": "defaultBranch", "value": ""},
                {"key": "owner", "value": "local"},
                {"key": "stars", "value": "0"},
                {"key": "language", "value": ""},
                {"key": "source", "value": "contracts"},
            ],
            "relations": [],
            "data": {
                "properties": config_properties,
                "formFields": form_fields,
                "rawTtl": raw_ttl,
                **contract.to_data(),
            },
        }
        document["data"]["ports"] = [
            port.to_dict() for port in ports_for_component(document)
        ]
        return document
