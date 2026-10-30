"""Проверка структуры OpenAPI, примеров и основных инвариантов контракта."""

from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parent
METHODS = {"get", "post", "put", "patch", "delete"}


def load_contract():
    return yaml.safe_load((ROOT / "openapi.yaml").read_text())


def resolve(spec, value):
    while isinstance(value, dict) and "$ref" in value:
        pointer = value["$ref"]
        if not pointer.startswith("#/"):
            raise ValueError(f"Неожиданная внешняя ссылка: {pointer}")
        value = spec
        for segment in pointer[2:].split("/"):
            value = value[segment.replace("~1", "/").replace("~0", "~")]
    return value


def instance_validator(spec, schema):
    # components используется как контейнер локальных $ref в полном документе.
    return Draft202012Validator(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            **schema,
            "components": spec["components"],
        },
        format_checker=FormatChecker(),
    )


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(spec):
    validate(spec)
    require(spec["openapi"].startswith("3.1."), "Нужен OpenAPI 3.1")
    require(
        any(s["url"].rstrip("/").endswith("/v1") for s in spec["servers"]),
        "Нет базового пути /v1/",
    )
    require(
        {t["name"] for t in spec["tags"]} == {"Books", "Readers", "Loans"},
        "Неверные tags",
    )
    for path in ("/books", "/readers"):
        require(
            {"get", "post"} <= spec["paths"][path].keys(), f"Неполная коллекция {path}"
        )
        require(
            {"get", "put", "patch", "delete"} <= spec["paths"][path + "/{id}"].keys(),
            f"Неполный CRUD {path}",
        )
    require("post" in spec["paths"]["/loans/{id}/return"], "Нет операции возврата")
    require("delete" not in spec["paths"]["/loans/{id}"], "Выдачи не удаляются")

    operations, checked_examples, operation_ids = 0, 0, set()
    for path, item in spec["paths"].items():
        require("_" not in path, f"URL не соответствует kebab-case: {path}")
        for method, operation in item.items():
            if method not in METHODS:
                continue
            operations += 1
            require(
                operation["operationId"] not in operation_ids, "operationId повторяется"
            )
            operation_ids.add(operation["operationId"])
            require(
                operation.get("summary") and operation.get("description"),
                "Пустое описание операции",
            )
            media_objects = []
            if "requestBody" in operation:
                media_objects.append(
                    operation["requestBody"]["content"]["application/json"]
                )
            for status, raw_response in operation["responses"].items():
                response = resolve(spec, raw_response)
                if status == "204":
                    require("content" not in response, "204 не должен содержать тело")
                if "content" in response:
                    media = response["content"]["application/json"]
                    media_objects.append(media)
                    if int(status) >= 400:
                        require(
                            media["schema"]
                            == {"$ref": "#/components/schemas/ErrorResponse"},
                            "Ошибка не использует ErrorResponse",
                        )
                        for ex in media.get("examples", {}).values():
                            code = resolve(spec, ex)["value"]["code"]
                            require(
                                code in response["x-error-codes"],
                                "Код примера не разрешён для ответа",
                            )
            for media in media_objects:
                validator = instance_validator(spec, media["schema"])
                for raw_example in media.get("examples", {}).values():
                    validator.validate(resolve(spec, raw_example)["value"])
                    checked_examples += 1

    for path in ("/books", "/readers", "/loans"):
        names = {
            resolve(spec, p)["name"] for p in spec["paths"][path]["get"]["parameters"]
        }
        require({"limit", "offset", "sort"} < names, f"Нет фильтров/пагинации: {path}")
    status_param = next(
        p
        for p in spec["paths"]["/loans"]["get"]["parameters"]
        if p.get("name") == "status"
    )
    require("overdue" in status_param["schema"]["enum"], "Нет overdue")
    schemas = spec["components"]["schemas"]
    require(
        schemas["Book"]["properties"]["available_copies"]["readOnly"],
        "available_copies не readOnly",
    )
    require(
        "null" in schemas["Loan"]["properties"]["returned_at"]["type"],
        "returned_at не допускает null",
    )
    negatives = 0
    for schema_name, example_name, extra in [
        ("CreateBookRequest", "CreateBook", {"available_copies": 1}),
        ("CreateBookRequest", "CreateBook", {"id": 1}),
        (
            "CreateReaderRequest",
            "CreateReader",
            {"registered_at": "2026-10-30T00:00:00Z"},
        ),
        ("CreateLoanRequest", "CreateLoan", {"returned_at": None}),
        ("CreateLoanRequest", "CreateLoan", {"due_date": "not-a-date"}),
        ("PatchBookRequest", None, {}),
        ("PatchReaderRequest", None, {}),
    ]:
        payload = {
            **(
                spec["components"]["examples"][example_name]["value"]
                if example_name
                else {}
            ),
            **extra,
        }
        validator = instance_validator(
            spec, {"$ref": f"#/components/schemas/{schema_name}"}
        )
        try:
            validator.validate(payload)
        except ValidationError:
            negatives += 1
        else:
            raise ValueError(f"Некорректный запрос принят схемой {schema_name}")
    for name, schema in schemas.items():
        if name.endswith("Request"):
            require(
                schema["additionalProperties"] is False,
                f"Неизвестные поля разрешены: {name}",
            )
    header = resolve(spec, spec["paths"]["/loans"]["post"]["parameters"][0])
    require(
        header["name"] == "Idempotency-Key" and header["required"],
        "Нет обязательного ключа",
    )
    policy = spec["x-idempotency-policy"]
    require(policy["ttl_hours"] == 24, "Неверный TTL")
    require(
        all(
            policy.get(k)
            for k in ("replay", "mismatch", "concurrent", "failure", "expiry")
        ),
        "Неполная политика ключей",
    )
    require(len(spec["x-business-rules"]) == 8, "Неполный перечень бизнес-правил")
    require(len(spec["components"]["examples"]) >= 3, "Мало примеров")
    print("OpenAPI 3.1: valid")
    print(
        f"Operations: {operations}; named examples: {len(spec['components']['examples'])}"
    )
    print(f"Request/response examples validated: {checked_examples}")
    print(f"Invalid requests correctly rejected by schemas: {negatives}")
    print("CRUD, error schemas, filters, paging and idempotency policy: checked")
    print(
        "Dynamic business rules are documented; their execution requires a real server."
    )


if __name__ == "__main__":
    verify(load_contract())
