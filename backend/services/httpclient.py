from flask import jsonify

"""
These helpers just pass through arguments to requests, but add some defaults
"""
def get(is_json: bool=True, *args, **kwargs):
    response = requests.get(
        *args,
        **kwargs,
        timeout=120,
        stream=True
    )

    if response.status_code in (401, 403):
        raise HTTPException(status_code=response.status_code, detail='Access denied. Your session may have expired.')

    if response.status_code > 399:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    if is_json:
        return response.json()

    return response
