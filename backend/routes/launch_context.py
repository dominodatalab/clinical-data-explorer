"""Launch-context routes for Domino app entry points."""

from flask import Blueprint, jsonify

from backend.services.launch_context import resolve_launch_context

bp = Blueprint("launch_context", __name__)


@bp.route("/launch-context", methods=["GET"])
def get_launch_context():
    """GET /launch-context resolves bare Domino app launches to extension URLs.

    The response is JSON with an `available` flag and a `redirectPath` when the
    current app run can be matched to a Domino project. The browser combines
    that path with the public Domino origin. Missing local run context returns
    `available: false` so local development can continue without a redirect.
    """
    return jsonify(resolve_launch_context())
