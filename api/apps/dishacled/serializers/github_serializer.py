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

            if filter_type == "selection" and "related_to_pipeline" in str(
                filter_key
            ):
                # The panel's "components of this pipeline" listing: resolved
                # server-side from the pipeline's relations, so a fresh save is
                # visible without waiting for the client's parent refetch.
                if isinstance(filter_value, list):
                    ids = [v for v in filter_value if v]
                elif filter_value:
                    ids = [filter_value]
                else:
                    ids = []
                # empty means "no parent context": restrict to nothing rather
                # than falling through to the full discovery listing
                params["related_to_pipeline"] = ids
                continue

            if filter_type == "selection" and "suggest_for_pipeline" in str(
                filter_key
            ):
                # Shape-guided suggestions by pipeline id: the store resolves
                # the pipeline's chain tail itself (the picker context only
                # knows the parent id, not the relation values).
                if isinstance(filter_value, list):
                    ids = [v for v in filter_value if v]
                elif filter_value:
                    ids = [filter_value]
                else:
                    ids = []
                if ids:
                    params["suggest_for_pipeline"] = ids
                continue

            if filter_type == "selection" and "suggest_for_shape" in str(
                filter_key
            ):
                # Port-scoped suggestions: the picker was opened from one
                # output port, so the compatible shapes are known outright --
                # no pipeline resolution needed.
                if isinstance(filter_value, list):
                    iris = [v for v in filter_value if v]
                elif filter_value:
                    iris = [filter_value]
                else:
                    iris = []
                if iris:
                    params["suggest_for_shape"] = iris
                continue

            if filter_type == "selection" and "compatible_with" in str(filter_key):
                # Shape-guided suggestions: the pipeline's current component
                # ids travel through to the store, which floats components
                # whose input shape matches the last one's output shape.
                # Never an identifiers restriction; an empty list means "no
                # tail yet", so nothing to rank by.
                if isinstance(filter_value, list):
                    ids = [v for v in filter_value if v]
                elif filter_value:
                    ids = [filter_value]
                else:
                    ids = []
                if ids:
                    params["compat_ids"] = ids
                continue

            if filter_type == "selection" and "identifiers" in str(filter_key):
                if filter_value is None:
                    ids = []
                elif isinstance(filter_value, list):
                    ids = filter_value
                else:
                    ids = [filter_value]
                # The presence of an identifiers selection filter restricts the
                # result to exactly these identifiers. An empty list therefore
                # means "no matches" rather than "no restriction", so always
                # set the key (even when empty).
                params["identifiers"] = ids
        return params

    def from_elody_to_github(self, entity, **kwargs):
        return entity
