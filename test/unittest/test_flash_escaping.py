"""Flash messages are escaped: request values shown in a flash must not run as script."""
import pytest


@pytest.fixture
def ui(monkeypatch):
    from arm.ui import app, db, utils as ui_utils
    import arm.ui.routes  # noqa: F401 -- register the blueprints as runui does

    monkeypatch.setattr(ui_utils, "metadata_selector", lambda *args, **kwargs: None)
    with app.app_context():
        db.create_all()
        app.config.update(TESTING=True, LOGIN_DISABLED=True)
        yield app, db
        db.session.rollback()
        db.session.remove()


PAYLOAD = "<script>alert(1)</script>"


# Upstream also checked the /list_titles page; in A2RM that address redirects to the job page (C38),
# so the two base templates below are what render every flash message.
@pytest.mark.parametrize("base", ["base.html", "support/base_simple.html"])
def test_both_base_templates_escape_flash_messages(ui, base):
    from flask import flash, render_template_string

    app, db = ui
    with app.test_request_context("/"):
        flash(PAYLOAD, "danger")
        page = render_template_string(f"{{% extends '{base}' %}}")
    assert PAYLOAD not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
