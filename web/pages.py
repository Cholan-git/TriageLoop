"""Server-rendered pages: the application shell and the not-found page."""
from flask import Blueprint, g, render_template

from .security import PERMISSIONS, csrf_token, page_auth

bp = Blueprint("pages", __name__)


@bp.get("/")
@page_auth
def index():
    return render_template("index.html", csrf=csrf_token(), user=g.user,
                           permissions=sorted(PERMISSIONS[g.user["role"]]))


def not_found():
    return render_template("404.html"), 404
