"""Tests for the dashboard HTML template."""

from chat_to_cop.dashboard import DASHBOARD_HTML


class TestDashboardHTML:
    def test_is_valid_html(self):
        assert DASHBOARD_HTML.strip().startswith("<!DOCTYPE html>")
        assert "</html>" in DASHBOARD_HTML

    def test_contains_required_tables(self):
        assert 'id="updates-body"' in DASHBOARD_HTML
        assert 'id="entities-body"' in DASHBOARD_HTML

    def test_contains_stats_bar(self):
        assert 'id="stat-updates"' in DASHBOARD_HTML
        assert 'id="stat-entities"' in DASHBOARD_HTML
        assert 'id="stat-status"' in DASHBOARD_HTML

    def test_confidence_color_classes_defined(self):
        assert ".conf-high" in DASHBOARD_HTML
        assert ".conf-mid" in DASHBOARD_HTML
        assert ".conf-low" in DASHBOARD_HTML

    def test_confidence_thresholds_in_js(self):
        assert "c > 0.8" in DASHBOARD_HTML
        assert "c >= 0.5" in DASHBOARD_HTML

    def test_method_tag_classes(self):
        assert ".method-llm" in DASHBOARD_HTML
        assert ".method-regex" in DASHBOARD_HTML
        assert ".method-passthrough" in DASHBOARD_HTML

    def test_auto_refresh_interval(self):
        assert "setInterval(refresh, 5000)" in DASHBOARD_HTML

    def test_fetches_api_endpoints(self):
        assert "fetch('/updates" in DASHBOARD_HTML
        assert "fetch('/entities')" in DASHBOARD_HTML
        assert "fetch('/health')" in DASHBOARD_HTML

    def test_entity_affiliation_classes(self):
        assert ".affil-friend" in DASHBOARD_HTML
        assert ".affil-hostile" in DASHBOARD_HTML
        assert ".affil-neutral" in DASHBOARD_HTML

    def test_status_classes(self):
        assert ".status-ok" in DASHBOARD_HTML
        assert ".status-degraded" in DASHBOARD_HTML
        assert ".status-inop" in DASHBOARD_HTML

    def test_escape_html_function(self):
        assert "function escapeHtml" in DASHBOARD_HTML

    def test_no_external_dependencies(self):
        assert "<link" not in DASHBOARD_HTML
        assert 'src="http' not in DASHBOARD_HTML
        assert "cdn" not in DASHBOARD_HTML.lower()

    def test_pause_button_present(self):
        assert 'id="pause-btn"' in DASHBOARD_HTML
        assert "WRITES ACTIVE" in DASHBOARD_HTML

    def test_pause_button_css_classes(self):
        assert ".pause-btn" in DASHBOARD_HTML
        assert ".state-active" in DASHBOARD_HTML
        assert ".state-paused" in DASHBOARD_HTML

    def test_pause_toggle_function(self):
        assert "function togglePause()" in DASHBOARD_HTML

    def test_pause_polls_admin_status(self):
        assert "fetch('/admin/status')" in DASHBOARD_HTML

    def test_pause_calls_admin_endpoints(self):
        assert "'/admin/resume'" in DASHBOARD_HTML
        assert "'/admin/pause'" in DASHBOARD_HTML

    def test_f12_keyboard_shortcut(self):
        assert "e.key === 'F12'" in DASHBOARD_HTML

    def test_queue_depth_element(self):
        assert 'id="queue-depth"' in DASHBOARD_HTML

    def test_pause_button_high_contrast_colors(self):
        """The button must be visible from across the room -- verify high-contrast colors."""
        assert "#238636" in DASHBOARD_HTML  # green bg for active
        assert "#da3633" in DASHBOARD_HTML  # red bg for paused

    def test_pause_button_large_text(self):
        """Button text must be readable from a distance."""
        assert "font-size: 1.3em" in DASHBOARD_HTML

    def test_updates_table_headers(self):
        assert "<th>Timestamp</th>" in DASHBOARD_HTML
        assert "<th>Channel</th>" in DASHBOARD_HTML
        assert "<th>Speaker</th>" in DASHBOARD_HTML
        assert "<th>Type</th>" in DASHBOARD_HTML
        assert "<th>Confidence</th>" in DASHBOARD_HTML
        assert "<th>Method</th>" in DASHBOARD_HTML
        assert "<th>Entities</th>" in DASHBOARD_HTML
        assert "<th>Message</th>" in DASHBOARD_HTML

    def test_entities_table_headers(self):
        assert "<th>Callsign / Track</th>" in DASHBOARD_HTML
        assert "<th>Platform Type</th>" in DASHBOARD_HTML
        assert "<th>Affiliation</th>" in DASHBOARD_HTML
        assert "<th>Status</th>" in DASHBOARD_HTML
        assert "<th>Last Updated</th>" in DASHBOARD_HTML
