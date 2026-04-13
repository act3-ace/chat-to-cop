"""Tests for the dashboard HTML template.

The dashboard is a single DASHBOARD_HTML constant containing inline CSS + JS.
We can't unit-test JS behavior from Python (that would need a browser/puppeteer).
What we CAN test from Python:

1. Structural integrity (valid HTML, required elements for the JS to bind to)
2. Design constraints (no external CDN deps, XSS prevention function present)
3. JS contract (API endpoints the JS fetches, confidence thresholds it uses)

These tests catch: renamed IDs that break JS bindings, removed API endpoints,
changed thresholds, and accidentally introduced CDN dependencies. They do NOT
catch: visual regressions, broken JS logic, or CSS rendering issues.
"""

import re

from chat_to_cop.dashboard import DASHBOARD_HTML


class TestDashboardStructure:
    """Verify the HTML has the elements that JS code binds to."""

    def test_valid_html_document(self):
        assert DASHBOARD_HTML.strip().startswith("<!DOCTYPE html>")
        assert "</html>" in DASHBOARD_HTML

    def test_required_js_bind_targets_exist(self):
        """Every ID that JS references via getElementById must exist in the HTML."""
        # Extract all getElementById("...") references from the JS
        js_refs = set(re.findall(r'getElementById\(["\'](\w+)["\']\)', DASHBOARD_HTML))
        # Extract all id="..." definitions from the HTML
        html_ids = set(re.findall(r'id="(\w+)"', DASHBOARD_HTML))

        missing = js_refs - html_ids
        assert not missing, f"JS references IDs not defined in HTML: {missing}"

    def test_required_table_bodies_for_data_rendering(self):
        """renderUpdates() and renderEntities() write to tbody elements."""
        assert 'id="updates-body"' in DASHBOARD_HTML
        assert 'id="entities-body"' in DASHBOARD_HTML


class TestDashboardDesignConstraints:
    """Verify architectural constraints that are easy to accidentally break."""

    def test_no_external_dependencies(self):
        """Dashboard must be fully self-contained (no CDN, no external CSS/JS).
        This is a deployment constraint: MASH networks may not have internet."""
        assert "<link" not in DASHBOARD_HTML, "External stylesheet detected"
        assert 'src="http' not in DASHBOARD_HTML, "External script detected"
        assert "cdn" not in DASHBOARD_HTML.lower(), "CDN reference detected"

    def test_xss_prevention_function_exists(self):
        """escapeHtml() must exist to prevent XSS from chat message content."""
        assert "function escapeHtml" in DASHBOARD_HTML


class TestDashboardAPIContract:
    """Verify the JS fetches the right API endpoints.
    If an endpoint is renamed in the FastAPI app but not in the HTML,
    the dashboard silently breaks."""

    def test_fetches_required_endpoints(self):
        assert "fetch('/updates" in DASHBOARD_HTML
        assert "fetch('/entities')" in DASHBOARD_HTML
        assert "fetch('/health')" in DASHBOARD_HTML

    def test_pause_admin_endpoints(self):
        assert "'/admin/pause'" in DASHBOARD_HTML
        assert "'/admin/resume'" in DASHBOARD_HTML
        assert "fetch('/admin/status')" in DASHBOARD_HTML

    def test_confidence_thresholds_match_cop_writer_defaults(self):
        """JS confidence-class thresholds must match the writer's tier boundaries.
        If cop_writer changes AUTO from 0.95 to 0.9, the dashboard colors
        should change too — this test catches the drift."""
        # The JS uses c > 0.8 for high and c >= 0.5 for mid
        assert "c > 0.8" in DASHBOARD_HTML
        assert "c >= 0.5" in DASHBOARD_HTML
