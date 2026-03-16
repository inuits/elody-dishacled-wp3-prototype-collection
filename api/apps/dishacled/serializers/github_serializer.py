import re


RUNTIME_INDICATORS = {
    "ts": ["typescript", "ts-", ".ts", "node"],
    "py": ["python", "py-", ".py", "pip"],
    "jvm": ["java", "jvm", "kotlin", "scala", ".jar"],
}


def _detect_runtime(repo: dict) -> str:
    language = (repo.get("language") or "").lower()
    name = (repo.get("name") or "").lower()
    description = (repo.get("description") or "").lower()
    combined = f"{language} {name} {description}"

    if language in ("typescript", "javascript") or any(
        ind in combined for ind in RUNTIME_INDICATORS["ts"]
    ):
        return "ts"
    if language == "python" or any(
        ind in combined for ind in RUNTIME_INDICATORS["py"]
    ):
        return "py"
    if language in ("java", "kotlin", "scala") or any(
        ind in combined for ind in RUNTIME_INDICATORS["jvm"]
    ):
        return "jvm"

    return "unknown"


class GithubSerializer:
    def from_github_to_elody(self, entity, **kwargs):
        repo = entity
        runtime = _detect_runtime(repo)
        encoded_id = repo["full_name"].replace("/", "--")
        return {
            "_id": encoded_id,
            "identifiers": [encoded_id, repo["full_name"]],
            "type": "githubProcessor",
            "metadata": [
                {"key": "name", "value": repo.get("name", "")},
                {"key": "description", "value": repo.get("description") or ""},
                {"key": "url", "value": repo.get("html_url", "")},
                {"key": "runtime", "value": runtime},
                {"key": "defaultBranch", "value": repo.get("default_branch", "main")},
                {"key": "owner", "value": repo.get("owner", {}).get("login", "")},
                {"key": "stars", "value": str(repo.get("stargazers_count", 0))},
                {"key": "language", "value": repo.get("language") or ""},
            ],
            "relations": [],
        }

    def from_elody_filter_to_github_filter(self, filters, **kwargs):
        params = {}
        for f in filters:
            filter_type = f.get("type")
            filter_value = f.get("value")
            filter_key = f.get("key", [])

            if filter_type == "type":
                continue

            if filter_type == "text" and filter_value and filter_value != "*":
                params["q_extra"] = filter_value

            if filter_type == "selection" and "identifiers" in str(filter_key):
                ids = filter_value if isinstance(filter_value, list) else [filter_value]
                if ids:
                    params["identifiers"] = ids
        return params

    def from_elody_to_github(self, entity, **kwargs):
        return entity
