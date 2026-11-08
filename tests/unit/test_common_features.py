"""Unit tests for Feature Flags, Migration Safety, and Rollback utilities."""
import subprocess
from unittest.mock import MagicMock, patch
from django.core.management import call_command, CommandError
from django.http import HttpRequest
import pytest
from rest_framework.response import Response
from rest_framework import status
import django.db.migrations as migrations
from django.db.models.fields import NOT_PROVIDED, CharField

from apps.common.flags import FeatureFlagManager, feature_flag_gate, flags
from apps.common.management.commands.check_migration_safety import Command as CheckMigrationCommand, MigrationIssue
from apps.common.management.commands.rollback_deployment import Command as RollbackCommand


# ============================================================================
# 1. Feature Flag Subsystem Tests
# ============================================================================

def test_feature_flag_manager_settings_fallback() -> None:
    mgr = FeatureFlagManager()
    with patch("django.conf.settings.FEATURE_FLAGS", {"TEST_FLAG": True}):
        assert mgr.is_enabled("TEST_FLAG") is True
        assert mgr.is_enabled("NON_EXISTENT_FLAG", default=False) is False


def test_feature_flag_manager_local_cache_and_set() -> None:
    mgr = FeatureFlagManager()
    mgr.set_flag("DYNAMIC_FLAG", "on")
    assert mgr.is_enabled("DYNAMIC_FLAG") is True

    # Test boolean evaluation
    mgr.set_flag("DYNAMIC_FLAG", "off")
    assert mgr.is_enabled("DYNAMIC_FLAG") is False

    mgr.delete_flag("DYNAMIC_FLAG")
    assert mgr.is_enabled("DYNAMIC_FLAG", default=False) is False

    mgr.clear_local_cache()


def test_feature_flag_string_representations() -> None:
    mgr = FeatureFlagManager()
    for trues in ["1", "true", "on", "yes", "True", "ENABLED"]:
        mgr.set_flag("STR_FLAG", trues)
        assert mgr.is_enabled("STR_FLAG") is True

    for falses in ["0", "false", "off", "no", "False", "DISABLED"]:
        mgr.set_flag("STR_FLAG", falses)
        assert mgr.is_enabled("STR_FLAG") is False


def test_feature_flag_percentage_rollout_deterministic() -> None:
    mgr = FeatureFlagManager()
    mgr.set_flag("CANARY_PERCENT", "50%")

    # Consistent results for the same key
    r1 = mgr.is_enabled("CANARY_PERCENT", key="user_12345")
    r2 = mgr.is_enabled("CANARY_PERCENT", key="user_12345")
    assert r1 == r2

    # Boundaries: 0% and 100%
    mgr.set_flag("ZERO_PERCENT", "0%")
    assert mgr.is_enabled("ZERO_PERCENT", key="user_123") is False

    mgr.set_flag("FULL_PERCENT", 100)
    assert mgr.is_enabled("FULL_PERCENT", key="user_123") is True

    # Invalid percentage format
    mgr.set_flag("BAD_PERCENT", "not_a_pct%")
    assert mgr.is_enabled("BAD_PERCENT", key="user_123", default=False) is False


def test_feature_flag_cache_exceptions_handled_gracefully() -> None:
    mgr = FeatureFlagManager()
    with patch("django.core.cache.cache.get", side_effect=RuntimeError("Redis down")):
        val = mgr.get_flag_value("CACHE_FAIL", default=False)
        assert val is False

    with patch("django.core.cache.cache.set", side_effect=RuntimeError("Redis down")):
        mgr.set_flag("FAIL_SET", True)

    with patch("django.core.cache.cache.delete", side_effect=RuntimeError("Redis down")):
        mgr.delete_flag("FAIL_DEL")


def test_feature_flag_request_overrides() -> None:
    mgr = FeatureFlagManager()
    mgr.set_flag("HEADER_FLAG", False)

    req = HttpRequest()
    req.headers = {"X-Feature-Flag": "HEADER_FLAG=true, OTHER=false"}
    req.GET = {}

    assert mgr.is_enabled("HEADER_FLAG", request=req) is True

    # Test Query Param override
    req2 = HttpRequest()
    req2.headers = {}
    req2.GET = {"_flag_QUERY_FLAG": "1"}

    assert mgr.is_enabled("QUERY_FLAG", request=req2) is True


def test_feature_flag_gate_decorator() -> None:
    @feature_flag_gate("GATED_FEATURE")
    def dummy_view(request: HttpRequest) -> Response:
        return Response({"status": "ok"})

    req = HttpRequest()
    req.headers = {}
    req.GET = {}

    # Disabled -> 404
    flags.set_flag("GATED_FEATURE", False)
    resp = dummy_view(req)
    assert resp.status_code == status.HTTP_404_NOT_FOUND
    assert resp.data["error"]["code"] == "FEATURE_DISABLED"

    # Enabled -> 200
    flags.set_flag("GATED_FEATURE", True)
    resp_ok = dummy_view(req)
    assert resp_ok.status_code == status.HTTP_200_OK
    assert resp_ok.data["status"] == "ok"


def test_feature_flag_gate_decorator_with_fallback() -> None:
    def fallback(request: HttpRequest) -> Response:
        return Response({"status": "legacy"})

    @feature_flag_gate("NEW_ALGO", fallback_view=fallback)
    def active_view(request: HttpRequest) -> Response:
        return Response({"status": "new"})

    req = HttpRequest()
    req.headers = {}
    req.GET = {}

    flags.set_flag("NEW_ALGO", False)
    resp = active_view(req)
    assert resp.data["status"] == "legacy"


# ============================================================================
# 2. Database Migration Safety Linter Tests
# ============================================================================

@pytest.mark.django_db
def test_check_migration_safety_clean_repository() -> None:
    call_command("check_migration_safety", app="stations", unapplied_only=False)
    call_command("check_migration_safety", unapplied_only=True)


def test_check_migration_safety_detects_unsafe_operations() -> None:
    cmd = CheckMigrationCommand()

    class DummyMigration:
        operations = [
            migrations.AddField(
                model_name="station",
                name="unsafe_col",
                field=CharField(max_length=50, null=False, default=NOT_PROVIDED),
            ),
            migrations.RemoveField(
                model_name="station",
                name="old_col",
            ),
            migrations.RenameField(
                model_name="station",
                old_name="name",
                new_name="station_name",
            ),
            migrations.RenameModel(
                old_name="OldStation",
                new_name="NewStation",
            ),
            migrations.RunSQL("DROP TABLE legacy_data;"),
            migrations.RunSQL(["CREATE TABLE foo;", "TRUNCATE legacy_table;"]),
            migrations.AlterField(
                model_name="station",
                name="price",
                field=CharField(max_length=10, null=False, default=NOT_PROVIDED),
            ),
        ]

    issues = cmd._inspect_migration("test_app", "0001_unsafe", DummyMigration())
    rule_ids = {i.rule_id for i in issues}

    assert "RULE-MIG-01-NOT-NULL-NO-DEFAULT" in rule_ids
    assert "RULE-MIG-02-REMOVE-FIELD" in rule_ids
    assert "RULE-MIG-03-RENAME-FIELD" in rule_ids
    assert "RULE-MIG-04-RENAME-MODEL" in rule_ids
    assert "RULE-MIG-05-DESTRUCTIVE-RAW-SQL" in rule_ids
    assert "RULE-MIG-06-ALTER-FIELD-NOT-NULL" in rule_ids


@pytest.mark.django_db
def test_check_migration_safety_command_fails_on_issues() -> None:
    unsafe_issue = MigrationIssue(
        app_label="test_app",
        migration_name="0001_test",
        severity="ERROR",
        rule_id="RULE-MIG-01-NOT-NULL-NO-DEFAULT",
        message="Unsafe operation detected",
    )
    with patch.object(CheckMigrationCommand, "_inspect_migration", return_value=[unsafe_issue]):
        with pytest.raises(CommandError, match="Database migration safety validation failed"):
            call_command("check_migration_safety")


# ============================================================================
# 3. Rollback CLI Tests
# ============================================================================

def test_rollback_cli_k8s_dry_run() -> None:
    call_command(
        "rollback_deployment",
        target="k8s",
        namespace="production",
        to_revision=4,
        dry_run=True,
    )


def test_rollback_cli_docker_dry_run() -> None:
    call_command(
        "rollback_deployment",
        target="docker",
        dry_run=True,
    )


def test_rollback_cli_k8s_live_success() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="deployment.apps/fuel-route-optimizer rolled back")
        call_command(
            "rollback_deployment",
            target="k8s",
            namespace="production",
            to_revision=2,
            dry_run=False,
        )
        assert mock_run.call_count == 2


def test_rollback_cli_k8s_live_failure_raises() -> None:
    with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "kubectl", stderr="error: deployment not found")):
        with pytest.raises(CommandError, match="Kubernetes rollback failed"):
            call_command(
                "rollback_deployment",
                target="k8s",
                namespace="production",
                dry_run=False,
            )


def test_rollback_cli_docker_live_success() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="Container api restarted")
        call_command(
            "rollback_deployment",
            target="docker",
            dry_run=False,
        )
        assert mock_run.call_count == 1


def test_rollback_cli_docker_live_failure_raises() -> None:
    with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "docker", stderr="error: container not running")):
        with pytest.raises(CommandError, match="Docker rollback failed"):
            call_command(
                "rollback_deployment",
                target="docker",
                dry_run=False,
            )


def test_rollback_cli_health_verification_success() -> None:
    cmd = RollbackCommand()
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"status": "healthy"}
    with patch("httpx.Client.get", return_value=mock_resp):
        cmd._verify_health("http://localhost:8000/api/v1/health/", timeout=5)


def test_rollback_cli_health_verification_timeout_raises() -> None:
    cmd = RollbackCommand()
    with patch("httpx.Client.get", side_effect=RuntimeError("Connection refused")):
        with pytest.raises(CommandError, match="Health verification timed out"):
            cmd._verify_health("http://localhost:8000/api/v1/health/", timeout=1)


# ============================================================================
# 4. Feature Flag CLI Tests
# ============================================================================

def test_feature_flag_cli_management() -> None:
    # Test default invocation (no options)
    call_command("feature_flag")

    # Test --list with empty settings
    with patch("django.conf.settings.FEATURE_FLAGS", {}):
        call_command("feature_flag", list=True)

    # Test --set
    call_command("feature_flag", set=["CLI_TEST_FLAG", "on"])
    assert flags.is_enabled("CLI_TEST_FLAG") is True

    # Test --get
    call_command("feature_flag", get="CLI_TEST_FLAG")

    # Test --delete
    call_command("feature_flag", delete="CLI_TEST_FLAG")
    assert flags.is_enabled("CLI_TEST_FLAG", default=False) is False


# ============================================================================
# 5. SRE Telemetry, SLI/SLO Evaluator, REST Views & CLI Auditor Tests
# ============================================================================

def test_sre_monitor_record_and_evaluate_compliant_and_breached() -> None:
    import io
    from apps.common.sre import SREMonitor, compute_percentile

    assert compute_percentile([], 99) == 0.0
    assert compute_percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == 3.0

    mon = SREMonitor()
    with patch.object(mon, "_inspect_data_readiness", return_value=(6626, True)):
        # 1. Compliant telemetry
        mon.record_request(
            compute_time_ms=4.2,
            total_latency_ms=45.0,
            cache_hit=False,
            external_calls=1,
            status_code=200,
        )
        mon.record_request(
            compute_time_ms=3.8,
            total_latency_ms=6.5,
            cache_hit=True,
            external_calls=0,
            status_code=200,
        )
        report = mon.evaluate_sla()
        assert report["sla_compliant"] is True
        assert report["overall_status"] == "MEETING_SLA"
        assert report["error_budget"]["error_budget_remaining_pct"] == 100.0

        prom_text = mon.render_prometheus_metrics()
        assert "spotter_sla_compliant 1" in prom_text
        assert "spotter_active_us_stations 6626" in prom_text

        # 2. Test when ENABLE_SRE_TELEMETRY is disabled
        flags.set_flag("ENABLE_SRE_TELEMETRY", False)
        try:
            mon.record_request(
                compute_time_ms=999.0,
                total_latency_ms=999.0,
                cache_hit=False,
                external_calls=10,
                status_code=500,
            )
            assert mon.evaluate_sla()["telemetry"]["total_requests"] == 2
        finally:
            flags.delete_flag("ENABLE_SRE_TELEMETRY")

        # 3. Breach availability (500 error), client error (400), and external call budget (> 3)
        mon.record_request(
            compute_time_ms=2.0,
            total_latency_ms=10.0,
            cache_hit=True,
            external_calls=0,
            status_code=400,
        )
        mon.record_request(
            compute_time_ms=25.0,
            total_latency_ms=500.0,
            cache_hit=False,
            external_calls=4,
            status_code=500,
        )
        breached_report = mon.evaluate_sla()
        assert breached_report["sla_compliant"] is False
        assert breached_report["overall_status"] == "BREACHED"
        assert breached_report["error_budget"]["budget_exhausted"] is True


@pytest.mark.django_db
def test_sre_rest_views_and_check_sla_command(client: object) -> None:
    import io
    from apps.common.sre import sre_monitor

    sre_monitor.reset()
    # Exercise real _inspect_data_readiness before mocking
    sre_monitor._inspect_data_readiness()

    with patch.object(sre_monitor, "_inspect_data_readiness", return_value=(6626, True)):
        sre_monitor.record_request(
            compute_time_ms=3.5,
            total_latency_ms=5.0,
            cache_hit=True,
            external_calls=0,
            status_code=200,
        )

        # 1. GET /api/v1/sre/sla/
        resp_sla = client.get("/api/v1/sre/sla/")
        assert resp_sla.status_code == 200
        data = resp_sla.json()
        assert data["sla_compliant"] is True
        assert len(data["slos"]) == 6

        # 2. GET /api/v1/sre/metrics/
        resp_prom = client.get("/api/v1/sre/metrics/")
        assert resp_prom.status_code == 200
        assert "spotter_sla_compliant 1" in resp_prom.content.decode("utf-8")

        # 3. CLI check_sla with synthetic probe, --json, and --prometheus
        out_table = io.StringIO()
        with patch("apps.stations.models.FuelStation.objects.filter") as mock_filter:
            mock_filter.return_value.count.return_value = 6626
            call_command("check_sla", stdout=out_table)
        assert "MEETING_SLA" in out_table.getvalue()

        out_json = io.StringIO()
        call_command("check_sla", no_probe=True, as_json=True, stdout=out_json)
        assert '"sla_compliant": true' in out_json.getvalue().lower()

        out_prom = io.StringIO()
        call_command("check_sla", no_probe=True, prometheus=True, stdout=out_prom)
        assert "spotter_sla_compliant 1" in out_prom.getvalue()

        # 4. CLI check_sla with --remote-url (success and error)
        mock_remote_resp = MagicMock()
        mock_remote_resp.json.return_value = data
        mock_remote_resp.raise_for_status.return_value = None
        with patch("httpx.Client.get", return_value=mock_remote_resp):
            out_remote = io.StringIO()
            call_command("check_sla", remote_url="https://api.example.com", stdout=out_remote)
            assert "MEETING_SLA" in out_remote.getvalue()

        with patch("httpx.Client.get", side_effect=RuntimeError("Remote unreachable")):
            with pytest.raises(CommandError, match="Failed to fetch remote SRE SLA report"):
                call_command("check_sla", remote_url="https://api.example.com")

        # 5. CLI check_sla breach with --auto-rollback --dry-run
        sre_monitor.record_request(
            compute_time_ms=50.0,
            total_latency_ms=900.0,
            cache_hit=False,
            external_calls=5,
            status_code=500,
        )
        out_breach = io.StringIO()
        with pytest.raises(CommandError, match="SLA compliance check FAILED"):
            call_command(
                "check_sla",
                no_probe=True,
                auto_rollback=True,
                rollback_target="k8s",
                dry_run=True,
                stdout=out_breach,
            )
        assert "[SRE AUTO-ROLLBACK]" in out_breach.getvalue()
        sre_monitor.reset()
