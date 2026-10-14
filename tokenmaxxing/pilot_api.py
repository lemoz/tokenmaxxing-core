"""Small explicit API schema; no generic SQL, file browser, or execution route."""

from .pilot_store import identifier
from .records import NormalizationError


def fields(data, required=(), optional=()):
    if not isinstance(data, dict) or set(data) - set(required) - set(optional) or set(required) - set(data):
        raise NormalizationError("invalid_request_fields")
    return data


def text(data, key, default=""):
    value = data.get(key, default)
    if not isinstance(value, str):
        raise NormalizationError("invalid_request_field_type")
    return value


def page(query):
    result = {}
    for key, default in (("offset", 0), ("limit", 50)):
        raw = query.get(key, str(default))
        if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal() or len(raw) > 9:
            raise NormalizationError("invalid_pagination")
        result[key] = int(raw)
    if not 1 <= result["limit"] <= 100:
        raise NormalizationError("page_limit_1_to_100")
    return result


def get(store, route, query):
    if route == "/api/overview":
        fields(query)
        return store.overview()
    if route in ("/api/sessions", "/api/outcomes"):
        fields(query, optional=("project", "offset", "limit"))
        return getattr(store, route.rsplit("/", 1)[1])(project=text(query, "project"), **page(query))
    if route == "/api/revisions":
        fields(query, ("session_id",))
        return store.revisions(identifier(query["session_id"]))
    if route == "/api/evidence/detail":
        fields(query, ("event_id",))
        return store.detail(identifier(query["event_id"], event=True))
    if route == "/api/evidence":
        keys = ("project", "provider", "query", "session_id", "revision_id", "category_filter")
        fields(query, optional=keys + ("offset", "limit"))
        args = {key: text(query, key) for key in keys}
        if args["provider"] not in ("", "codex", "claude", "hermes") or args["category_filter"] not in ("", "intent", "observed", "retained"):
            raise NormalizationError("invalid_evidence_filter")
        for key in ("session_id", "revision_id"):
            if args[key]:
                identifier(args[key])
        return store.evidence(**args, **page(query))
    raise NormalizationError("route_not_found")


def post(store, route, data):
    if route == "/api/import":
        fields(data, ("path", "format", "project"), ("selected_session",))
        return store.import_source(text(data, "path"), text(data, "format"), text(data, "project"),
                                   data.get("selected_session"))
    if route == "/api/move":
        fields(data, ("session_id", "project"))
        return store.move(identifier(data["session_id"]), text(data, "project"))
    if route == "/api/rename":
        fields(data, ("old", "new"))
        return store.rename(text(data, "old"), text(data, "new"))
    if route == "/api/delete":
        fields(data, ("session_id", "confirmation"))
        return store.delete(identifier(data["session_id"]), text(data, "confirmation"))
    if route == "/api/outcome":
        fields(data, ("session_id", "evidence", "kind"), ("claim", "note", "path", "expected"))
        kind = text(data, "kind")
        if kind == "report" and ("path" in data or "expected" in data):
            raise NormalizationError("invalid_request_fields")
        if kind == "check" and data.get("claim"):
            raise NormalizationError("artifact_assertion_is_fixed")
        return store.record_outcome(identifier(data["session_id"]), data["evidence"], kind,
                                    text(data, "claim"), text(data, "note"), data.get("path"), data.get("expected"))
    raise NormalizationError("route_not_found")
