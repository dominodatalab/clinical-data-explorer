DATAFRAME_EXPIRED_CODE = "DATAFRAME_EXPIRED"


def mcp_error_payload(response, fallback):
    try:
        detail = response.json().get("detail", fallback)
    except ValueError:
        detail = fallback

    if isinstance(detail, dict):
        error = detail.get("error") or detail.get("message") or fallback
        payload = {"error": error}
        if detail.get("code"):
            payload["code"] = detail["code"]
    else:
        payload = {"error": detail}

    if payload.get("code") == DATAFRAME_EXPIRED_CODE:
        payload.update({
            "code": DATAFRAME_EXPIRED_CODE,
            "description": "The backend data for this session has expired. Refresh the dataset to continue.",
        })
    return payload
