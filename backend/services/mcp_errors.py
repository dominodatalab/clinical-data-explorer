RELOAD_DATA_DESCRIPTION = "Please reload your data"


def mcp_error_payload(response, fallback):
    try:
        detail = response.json().get("detail", fallback)
    except ValueError:
        detail = fallback

    if isinstance(detail, dict):
        error = detail.get("error") or detail.get("message") or fallback
        payload = {"error": error}
        if detail.get("description"):
            payload["description"] = detail["description"]
    else:
        payload = {"error": detail}

    payload["description"] = RELOAD_DATA_DESCRIPTION
    return payload
