from apps.dishacled.serializers.github_serializer import GithubSerializer


MOCK_GITHUB_REPO = {
    "id": 123456,
    "name": "ldes-client",
    "full_name": "rdfc/ldes-client",
    "description": "An LDES client processor for RDF-Connect",
    "html_url": "https://github.com/rdfc/ldes-client",
    "default_branch": "main",
    "language": "TypeScript",
    "stargazers_count": 42,
    "owner": {
        "login": "rdfc",
    },
    "topics": ["rdfc-processor"],
}

MOCK_PYTHON_REPO = {
    "id": 789,
    "name": "py-log-processor",
    "full_name": "rdfc/py-log-processor",
    "description": "A Python log processor",
    "html_url": "https://github.com/rdfc/py-log-processor",
    "default_branch": "main",
    "language": "Python",
    "stargazers_count": 10,
    "owner": {
        "login": "rdfc",
    },
}

MOCK_JAVA_REPO = {
    "id": 456,
    "name": "rml-mapper",
    "full_name": "rdfc/rml-mapper",
    "description": "JVM-based RML mapper",
    "html_url": "https://github.com/rdfc/rml-mapper",
    "default_branch": "main",
    "language": "Java",
    "stargazers_count": 25,
    "owner": {
        "login": "rdfc",
    },
}


class TestGithubSerializer:
    def test_from_github_to_elody_basic_structure(self):
        serializer = GithubSerializer()
        result = serializer.from_github_to_elody(MOCK_GITHUB_REPO)

        assert result["_id"] == "rdfc--ldes-client"
        assert result["identifiers"] == ["rdfc--ldes-client", "rdfc/ldes-client"]
        assert result["type"] == "githubProcessor"
        assert isinstance(result["metadata"], list)
        assert isinstance(result["relations"], list)

    def test_from_github_to_elody_metadata(self):
        serializer = GithubSerializer()
        result = serializer.from_github_to_elody(MOCK_GITHUB_REPO)
        meta = {m["key"]: m["value"] for m in result["metadata"]}

        assert meta["name"] == "ldes-client"
        assert meta["description"] == "An LDES client processor for RDF-Connect"
        assert meta["url"] == "https://github.com/rdfc/ldes-client"
        assert meta["defaultBranch"] == "main"
        assert meta["owner"] == "rdfc"

    def test_runtime_detection_typescript(self):
        serializer = GithubSerializer()
        result = serializer.from_github_to_elody(MOCK_GITHUB_REPO)
        meta = {m["key"]: m["value"] for m in result["metadata"]}
        assert meta["runtime"] == "ts"

    def test_runtime_detection_python(self):
        serializer = GithubSerializer()
        result = serializer.from_github_to_elody(MOCK_PYTHON_REPO)
        meta = {m["key"]: m["value"] for m in result["metadata"]}
        assert meta["runtime"] == "py"

    def test_runtime_detection_jvm(self):
        serializer = GithubSerializer()
        result = serializer.from_github_to_elody(MOCK_JAVA_REPO)
        meta = {m["key"]: m["value"] for m in result["metadata"]}
        assert meta["runtime"] == "jvm"

    def test_from_elody_filter_to_github_filter_text(self):
        serializer = GithubSerializer()
        filters = [
            {"type": "type", "value": "githubProcessor"},
            {"type": "text", "value": "ldes", "key": ["name"]},
        ]
        result = serializer.from_elody_filter_to_github_filter(filters)
        assert result.get("q_extra") == "ldes"

    def test_from_elody_filter_to_github_filter_empty(self):
        serializer = GithubSerializer()
        filters = [{"type": "type", "value": "githubProcessor"}]
        result = serializer.from_elody_filter_to_github_filter(filters)
        assert result == {}

    def test_from_elody_filter_to_github_filter_wildcard(self):
        serializer = GithubSerializer()
        filters = [{"type": "text", "value": "*", "key": ["name"]}]
        result = serializer.from_elody_filter_to_github_filter(filters)
        assert result == {}

    def test_from_elody_to_github_passthrough(self):
        serializer = GithubSerializer()
        entity = {"_id": "test"}
        assert serializer.from_elody_to_github(entity) == entity

    def test_from_elody_filter_to_github_filter_identifiers(self):
        serializer = GithubSerializer()
        filters = [
            {"type": "type", "value": "githubProcessor"},
            {"type": "selection", "key": ["elody:1|identifiers"], "value": ["rdfc--ldes-client", "rdfc--rml-mapper"]},
        ]
        result = serializer.from_elody_filter_to_github_filter(filters)
        assert result.get("identifiers") == ["rdfc--ldes-client", "rdfc--rml-mapper"]

    def test_from_elody_filter_to_github_filter_empty_identifiers_selection(self):
        # A relation selection filter that resolved to no related entities must
        # produce an explicit empty identifiers list ("restrict to none"),
        # not be dropped (which would be treated as "no restriction").
        serializer = GithubSerializer()
        filters = [
            {"type": "type", "value": "githubProcessor"},
            {"type": "selection", "key": ["elody:1|identifiers"], "value": []},
        ]
        result = serializer.from_elody_filter_to_github_filter(filters)
        assert result.get("identifiers") == []

    def test_handles_missing_description(self):
        serializer = GithubSerializer()
        repo = {**MOCK_GITHUB_REPO, "description": None}
        result = serializer.from_github_to_elody(repo)
        meta = {m["key"]: m["value"] for m in result["metadata"]}
        assert meta["description"] == ""
