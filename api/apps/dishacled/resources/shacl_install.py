import re

from flask import Blueprint, request
from flask_restful import Api, Resource

from apps.dishacled.shacl.parser import ShaclParser
from apps.dishacled.resources.shacl_codegen import _fetch_ttl_from_repo, _find_main_shape


api_bp = Blueprint("shacl_install", __name__)
api = Api(api_bp)


def _camel_to_kebab(name):
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1-\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", s1).lower()


class ShaclInstall(Resource):
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

        parsed_properties = []
        for prop in properties:
            parsed_properties.append(
                {
                    "name": prop.name,
                    "inputFieldType": prop.input_field_type,
                    "isRequired": prop.is_required,
                    "inValues": prop.in_values if prop.in_values else [],
                    "classRef": prop.class_ref,
                }
            )

        processor_metadata = parser.parse_processor_metadata(ttl_content)

        import pymongo
        from os import getenv

        client = pymongo.MongoClient(
            getenv("MONGODB_HOSTS", "mongo"),
            int(getenv("MONGODB_PORT", 27017)),
        )
        db = client[getenv("MONGODB_DB_NAME", "elody")]

        identifier = f"processorDefinition:{type_name}"

        entity = {
            "type": "processorDefinition",
            "identifiers": [identifier],
            "metadata": [
                {
                    "key": "name",
                    "value": processor_metadata.get("label") or type_name,
                },
                {"key": "processorType", "value": type_name},
                {"key": "runnerType", "value": runner_type},
                {"key": "pillLabel", "value": pill_label},
            ],
            "data": {
                "properties": parsed_properties,
                "rawTtl": ttl_content,
            },
        }

        existing = db.entities.find_one({"identifiers": identifier})

        if existing:
            db.entities.update_one(
                {"_id": existing["_id"]}, {"$set": entity}
            )
            return {
                "message": f"Updated processorDefinition for {type_name}",
                "id": str(existing["_id"]),
            }, 200
        else:
            result = db.entities.insert_one(entity)
            return {
                "message": f"Created processorDefinition for {type_name}",
                "id": str(result.inserted_id),
            }, 201


class ShaclInstallAndCreate(Resource):
    def post(self):
        data = request.get_json()
        if not data:
            return {"error": "Request body is required"}, 400

        repo_url = data.get("repoUrl")
        type_name = data.get("typeName")
        runner_type = data.get("runnerType", "jsRunner")
        pill_label = data.get("pillLabel", "")
        pipeline_id = data.get("pipelineId")
        metadata = data.get("metadata", {})

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

        parsed_properties = []
        for prop in properties:
            parsed_properties.append(
                {
                    "name": prop.name,
                    "inputFieldType": prop.input_field_type,
                    "isRequired": prop.is_required,
                    "inValues": prop.in_values if prop.in_values else [],
                    "classRef": prop.class_ref,
                }
            )

        processor_metadata = parser.parse_processor_metadata(ttl_content)

        import pymongo
        from os import getenv

        client = pymongo.MongoClient(
            getenv("MONGODB_HOSTS", "mongo"),
            int(getenv("MONGODB_PORT", 27017)),
        )
        db = client[getenv("MONGODB_DB_NAME", "elody")]

        # Step 1: Upsert processorDefinition with TTL URL instead of rawTtl
        ttl_url = repo_url.replace(
            "github.com", "raw.githubusercontent.com"
        ).rstrip("/")
        definition_identifier = f"processorDefinition:{type_name}"

        definition_entity = {
            "type": "processorDefinition",
            "identifiers": [definition_identifier],
            "metadata": [
                {
                    "key": "name",
                    "value": processor_metadata.get("label") or type_name,
                },
                {"key": "processorType", "value": type_name},
                {"key": "runnerType", "value": runner_type},
                {"key": "pillLabel", "value": pill_label},
                {"key": "repoUrl", "value": repo_url},
            ],
            "data": {
                "properties": parsed_properties,
                "ttlSource": ttl_url,
            },
        }

        existing_def = db.entities.find_one({"identifiers": definition_identifier})
        if existing_def:
            db.entities.update_one(
                {"_id": existing_def["_id"]}, {"$set": definition_entity}
            )
        else:
            db.entities.insert_one(definition_entity)

        # Step 2: Create processor entity
        processor_metadata_list = [
            {"key": "name", "value": metadata.get("name", type_name)},
            {"key": "type", "value": type_name},
        ]
        for key, value in metadata.items():
            if key != "name":
                processor_metadata_list.append({"key": key, "value": str(value)})

        processor_entity = {
            "type": type_name,
            "metadata": processor_metadata_list,
            "relations": [],
        }

        processor_result = db.entities.insert_one(processor_entity)
        processor_id = str(processor_result.inserted_id)

        # Step 3: Add relation to pipeline if pipelineId provided
        if pipeline_id:
            from bson import ObjectId

            pipeline = db.entities.find_one({"_id": ObjectId(pipeline_id)})
            if pipeline:
                relations = pipeline.get("relations", [])
                relations.append(
                    {
                        "key": processor_id,
                        "type": "hasProcessor",
                    }
                )
                db.entities.update_one(
                    {"_id": ObjectId(pipeline_id)},
                    {"$set": {"relations": relations}},
                )

        return {
            "message": f"Created processor {type_name} from GitHub",
            "processorId": processor_id,
            "processorDefinitionType": type_name,
            "pipelineId": pipeline_id,
        }, 201


api.add_resource(ShaclInstall, "/shacl/install")
api.add_resource(ShaclInstallAndCreate, "/shacl/install-and-create")
