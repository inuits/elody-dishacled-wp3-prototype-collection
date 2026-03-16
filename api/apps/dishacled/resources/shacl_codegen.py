import io
import json
import zipfile

import requests
from flask import Blueprint, make_response, request
from flask_restful import Api, Resource

from apps.dishacled.shacl.codegen import CodeGenerator, CodegenConfig
from apps.dishacled.shacl.parser import ShaclParser

api_bp = Blueprint("shacl_codegen", __name__)
api = Api(api_bp)


class ShaclCodegen(Resource):
    def post(self):
        data = request.get_json()
        if not data:
            return {"error": "Request body is required"}, 400

        repo_url = data.get("repoUrl")
        type_name = data.get("typeName")
        runner_type = data.get("runnerType", "jsRunner")
        pill_label = data.get("pillLabel", "")

        if not repo_url or not type_name:
            return {"error": "repoUrl and typeName are required"}, 400

        ttl_content = _fetch_ttl_from_repo(repo_url)
        if not ttl_content:
            return {"error": "No .ttl files found in repository"}, 404

        parser = ShaclParser()
        shapes = parser.parse(ttl_content)

        if not shapes:
            return {"error": "No SHACL shapes found in .ttl files"}, 404

        main_shape_name = _find_main_shape(shapes)
        if not main_shape_name:
            return {"error": "Could not determine main processor shape"}, 400

        properties = shapes[main_shape_name]
        config = CodegenConfig(
            type_name=type_name,
            runner_type=runner_type,
            pill_label=pill_label,
        )
        generator = CodeGenerator(config, properties)
        files = generator.generate_all()

        zip_buffer = _create_zip(files, config)

        response = make_response(zip_buffer.getvalue())
        response.headers["Content-Type"] = "application/zip"
        response.headers[
            "Content-Disposition"
        ] = f"attachment; filename={config.snake_name}_codegen.zip"
        return response


def _fetch_ttl_from_repo(repo_url: str) -> str | None:
    parts = repo_url.rstrip("/").split("/")
    if len(parts) < 2:
        return None
    owner = parts[-2]
    repo = parts[-1]

    api_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/main?recursive=1"
    response = requests.get(
        api_url,
        headers={"Accept": "application/vnd.github+json"},
    )
    if response.status_code != 200:
        api_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/master?recursive=1"
        response = requests.get(
            api_url,
            headers={"Accept": "application/vnd.github+json"},
        )
        if response.status_code != 200:
            return None

    tree = response.json().get("tree", [])
    ttl_files = [item for item in tree if item["path"].endswith(".ttl")]
    if not ttl_files:
        return None

    combined_ttl = []
    for ttl_file in ttl_files:
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{parts[-1] if len(parts) > 2 else 'main'}/{ttl_file['path']}"
        content_response = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}/contents/{ttl_file['path']}",
            headers={"Accept": "application/vnd.github.raw+json"},
        )
        if content_response.status_code == 200:
            combined_ttl.append(content_response.text)

    return "\n".join(combined_ttl) if combined_ttl else None


def _find_main_shape(shapes: dict) -> str | None:
    if len(shapes) == 1:
        return list(shapes.keys())[0]

    for name, props in shapes.items():
        if len(props) > 2:
            return name

    return list(shapes.keys())[0] if shapes else None


def _create_zip(files: dict, config: CodegenConfig) -> io.BytesIO:
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        queries_filename = f"{config.type_name}.queries.ts"
        if queries_filename in files:
            zf.writestr(
                f"new-files/queries/entities/processors/{queries_filename}",
                files[queries_filename],
            )

        python_config_filename = f"{config.snake_name}_configuration.py"
        if python_config_filename in files:
            zf.writestr(
                f"new-files/object_configurations/{python_config_filename}",
                files[python_config_filename],
            )

        if "patches" in files:
            patches = files["patches"]
            patch_instructions = _generate_patch_instructions(patches, config)
            zf.writestr("PATCHES.md", patch_instructions)
            zf.writestr("patches/patches.json", json.dumps(patches, indent=2, default=str))

    zip_buffer.seek(0)
    return zip_buffer


def _generate_patch_instructions(patches: dict, config: CodegenConfig) -> str:
    c = config
    instructions = [
        f"# Code Generation Patches for {c.pascal_name}",
        "",
        "Apply these patches to the existing codebase files.",
        "",
    ]

    if "dishacledSchema.schema.ts" in patches:
        p = patches["dishacledSchema.schema.ts"]
        instructions.extend([
            "## 1. dishacledSchema.schema.ts",
            "",
            "Add to `Entitytyping` enum:",
            f"```",
            p["enum_entry"],
            "```",
            "",
            "Add type definition (after the last type):",
            "```graphql",
            p["type_definition"],
            "```",
            "",
        ])

    if "dishacledResolver.ts" in patches:
        p = patches["dishacledResolver.ts"]
        instructions.extend([
            "## 2. dishacledResolver.ts",
            "",
            "Add to `__resolveType` (before `return \"BaseEntity\"`):",
            "```typescript",
            p["resolve_type"],
            "```",
            "",
            "Add resolver (before `Query:`):",
            "```typescript",
            p["resolver_entry"],
            "```",
            "",
        ])

    if "en.json" in patches:
        instructions.extend([
            "## 3. translations/en.json",
            "",
            "Merge translation keys into existing JSON.",
            "",
        ])

    if "mappers.py" in patches:
        p = patches["mappers.py"]
        instructions.extend([
            "## 4. mappers.py",
            "",
            "Add import:",
            "```python",
            p["import_line"],
            "```",
            "",
            "Add to OBJECT_CONFIGURATION_MAPPER:",
            "```python",
            p["config_entry"],
            "```",
            "",
        ])

    if "pipeline.queries.ts" in patches and patches["pipeline.queries.ts"]:
        p = patches["pipeline.queries.ts"]
        instructions.extend([
            "## 5. pipeline.queries.ts — Relation metadata on processor entity list",
            "",
            p["description"],
            "",
            "Add this spread inside `... on GithubProcessor` in processor entity list results:",
            "```graphql",
            p["fragment_spread"],
            "```",
            "",
        ])

    if "runner.queries.ts" in patches and patches["runner.queries.ts"]:
        p = patches["runner.queries.ts"]
        instructions.extend([
            "## 6. runner.queries.ts — Relation metadata on processor entity list",
            "",
            p["description"],
            "",
            "Add this spread inside `... on GithubProcessor` in processor entity list results:",
            "```graphql",
            p["fragment_spread"],
            "```",
            "",
        ])

    return "\n".join(instructions)


api.add_resource(ShaclCodegen, "/shacl/generate")
