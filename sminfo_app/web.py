from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, render_template, request

from .sminfo_client import NotLoggedInError, SearchError, SminfoClient


def create_app() -> Flask:
    root_dir = Path(__file__).resolve().parents[1]
    app = Flask(
        __name__,
        template_folder=str(root_dir / "templates"),
        static_folder=str(root_dir / "static"),
    )

    state_path = os.getenv("SMINFO_STATE_PATH")
    timeout_ms = int(os.getenv("SMINFO_TIMEOUT_MS", "45000"))

    @app.get("/")
    def home_get():
        client = SminfoClient(state_path=state_path, timeout_ms=timeout_ms)
        return render_template(
            "index.html",
            query="",
            company="",
            result=None,
            error=None,
            has_session=client.has_saved_session(),
            login_status_text=client.get_login_status_text(),
            state_path=str(client.state_path),
        )

    @app.post("/")
    def home_post():
        query = request.form.get("query", "").strip()
        company = request.form.get("company", "").strip()

        client = SminfoClient(state_path=state_path, timeout_ms=timeout_ms)

        if not query:
            return render_template(
                "index.html",
                query=query,
                company=company,
                result=None,
                error="검색어를 입력하세요.",
                has_session=client.has_saved_session(),
                login_status_text=client.get_login_status_text(),
                state_path=str(client.state_path),
            )

        try:
            result = client.search_company(query=query, company_name=company or None).to_dict()
            error = None
        except (NotLoggedInError, SearchError, ValueError) as exc:
            result = None
            error = str(exc)

        return render_template(
            "index.html",
            query=query,
            company=company,
            result=result,
            error=error,
            has_session=client.has_saved_session(),
            login_status_text=client.get_login_status_text(),
            state_path=str(client.state_path),
        )

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5050"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
