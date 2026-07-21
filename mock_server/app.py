"""Mock Domino API server for local development and tests."""

import json
from pathlib import Path

from fastapi import FastAPI


RESPONSE_DATA_DIR = Path(__file__).resolve().parent / "response_data"


def load_response_data(file_name: str):
    with open(RESPONSE_DATA_DIR / file_name) as response_file:
        return json.load(response_file)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Clinical Data Explorer Mock Server",
        description="Mock external APIs used by Clinical Data Explorer.",
        version="1.0.0",
    )

    @app.get("/api/users/v1/self")
    async def get_self_user():
        return load_response_data("api_users_v1_self.json")

    return app


app = create_app()
