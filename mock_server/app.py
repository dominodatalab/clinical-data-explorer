"""Mock Domino API server for local development and tests."""

from fastapi import FastAPI


SELF_USER_RESPONSE = {
    "metadata": {
        "notices": [
            "string",
        ],
        "requestId": "string",
    },
    "user": {
        "avatarUrl": "string",
        "companyName": "string",
        "email": "string",
        "firstName": "string",
        "fullName": "string",
        "id": "string",
        "idpId": "string",
        "lastName": "string",
        "phoneNumber": "string",
        "roles": [
            "string",
        ],
        "userName": "string",
    },
}


def create_app() -> FastAPI:
    app = FastAPI(
        title="Clinical Data Explorer Mock Server",
        description="Mock external APIs used by Clinical Data Explorer.",
        version="1.0.0",
    )

    @app.get("/api/users/v1/self")
    async def get_self_user():
        return SELF_USER_RESPONSE

    return app


app = create_app()
