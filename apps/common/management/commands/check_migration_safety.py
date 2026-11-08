"""
CI/CD & Pre-Deployment Database Migration Safety Linter.

Enforces zero-downtime Expand-Contract migration rules to prevent production outages:
1. NO adding NOT NULL columns without an explicit default value.
2. NO direct column removals (migrations.RemoveField) without expand-contract lifecycle.
3. NO column or model renames (migrations.RenameField / RenameModel) in active pipelines.
4. NO raw destructive SQL statements (DROP TABLE, DROP COLUMN, TRUNCATE).
5. NO altering fields from nullable to NOT NULL without a default.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from django.core.management.base import BaseCommand, CommandError
from django.db.migrations.loader import MigrationLoader
from django.db import connection
from django.db.models.fields import NOT_PROVIDED
import django.db.migrations as migrations


@dataclass(slots=True, frozen=True)
class MigrationIssue:
    app_label: str
    migration_name: str
    severity: str  # "ERROR" or "WARNING"
    rule_id: str
    message: str


class Command(BaseCommand):
    help = "Validates Django migrations against zero-downtime and expand-contract safety rules."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--app",
            type=str,
            help="Check only migrations in the specified app.",
        )
        parser.add_argument(
            "--ignore-app",
            action="append",
            default=["auth", "contenttypes", "sessions", "admin"],
            help="Ignore third-party or built-in Django apps.",
        )
        parser.add_argument(
            "--fail-on-warning",
            action="store_true",
            default=True,
            help="Treat warnings as errors and exit with non-zero status.",
        )
        parser.add_argument(
            "--unapplied-only",
            action="store_true",
            help="Only inspect migrations that have not yet been applied to the current database.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        target_app = options.get("app")
        ignored_apps = set(options.get("ignore_app") or [])
        fail_on_warning = options.get("fail_on_warning", True)
        unapplied_only = options.get("unapplied_only", False)

        loader = MigrationLoader(connection, ignore_no_migrations=True)
        applied_keys = set(loader.applied_migrations.keys())

        issues: list[MigrationIssue] = []
        checked_count = 0

        for key, migration in loader.disk_migrations.items():
            app_label, migration_name = key
            if app_label in ignored_apps:
                continue
            if target_app and app_label != target_app:
                continue
            if unapplied_only and key in applied_keys:
                continue

            checked_count += 1
            migration_issues = self._inspect_migration(app_label, migration_name, migration)
            issues.extend(migration_issues)

        self.stdout.write(f"\nChecked {checked_count} migration(s) for zero-downtime safety compliance.")

        if not issues:
            self.stdout.write(self.style.SUCCESS("✓ All checked migrations comply with zero-downtime safety rules!\n"))
            return

        error_count = sum(1 for i in issues if i.severity == "ERROR")
        warn_count = sum(1 for i in issues if i.severity == "WARNING")

        self.stdout.write("\n" + "=" * 76)
        self.stdout.write(self.style.ERROR(f"FOUND {len(issues)} ZERO-DOWNTIME MIGRATION ISSUE(S):"))
        self.stdout.write("=" * 76)

        for issue in issues:
            prefix = self.style.ERROR("[ERROR]") if issue.severity == "ERROR" else self.style.WARNING("[WARN]")
            self.stdout.write(f"{prefix} [{issue.rule_id}] {issue.app_label}.{issue.migration_name}:")
            self.stdout.write(f"    {issue.message}\n")

        self.stdout.write(
            f"Summary: {error_count} Error(s), {warn_count} Warning(s).\n"
        )

        should_fail = error_count > 0 or (fail_on_warning and warn_count > 0)
        if should_fail:
            raise CommandError("Database migration safety validation failed. Please fix the unsafe operations above.")

    def _inspect_migration(
        self,
        app_label: str,
        migration_name: str,
        migration: Any,
    ) -> list[MigrationIssue]:
        issues: list[MigrationIssue] = []

        for op in migration.operations:
            # Rule 1: migrations.AddField NOT NULL without default
            if isinstance(op, migrations.AddField):
                field = op.field
                is_pk = getattr(field, "primary_key", False)
                if not field.null and field.default is NOT_PROVIDED and not is_pk:
                    issues.append(
                        MigrationIssue(
                            app_label=app_label,
                            migration_name=migration_name,
                            severity="ERROR",
                            rule_id="RULE-MIG-01-NOT-NULL-NO-DEFAULT",
                            message=(
                                f"Adding NOT NULL field '{op.name}' to model '{op.model_name}' without a default value. "
                                "This will lock the table and break INSERT queries from running N-1 application pods. "
                                "Fix: Set 'null=True' or provide a non-empty 'default'."
                            ),
                        )
                    )

            # Rule 2: migrations.RemoveField
            elif isinstance(op, migrations.RemoveField):
                issues.append(
                    MigrationIssue(
                        app_label=app_label,
                        migration_name=migration_name,
                        severity="WARNING",
                        rule_id="RULE-MIG-02-REMOVE-FIELD",
                        message=(
                            f"Removing field '{op.name}' from model '{op.model_name}'. "
                            "In zero-downtime deployments, instances running the previous code version will crash when selecting this column. "
                            "Fix: Follow Expand-Contract. (1) Stop reading/writing the field in code, (2) deploy, (3) drop field in next release."
                        ),
                    )
                )

            # Rule 3: migrations.RenameField
            elif isinstance(op, migrations.RenameField):
                issues.append(
                    MigrationIssue(
                        app_label=app_label,
                        migration_name=migration_name,
                        severity="ERROR",
                        rule_id="RULE-MIG-03-RENAME-FIELD",
                        message=(
                            f"Renaming field '{op.old_name}' -> '{op.new_name}' on model '{op.model_name}'. "
                            "Instant renames break live N-1 pods immediately. "
                            "Fix: Add new column, dual-write in application code, backfill data, and drop old column later."
                        ),
                    )
                )

            # Rule 4: migrations.RenameModel
            elif isinstance(op, migrations.RenameModel):
                issues.append(
                    MigrationIssue(
                        app_label=app_label,
                        migration_name=migration_name,
                        severity="ERROR",
                        rule_id="RULE-MIG-04-RENAME-MODEL",
                        message=(
                            f"Renaming model '{op.old_name}' -> '{op.new_name}'. "
                            "Fix: Create new model/table and migrate data using the Expand-Contract pattern."
                        ),
                    )
                )

            # Rule 5: migrations.RunSQL with destructive commands
            elif isinstance(op, migrations.RunSQL):
                sql_content = ""
                if isinstance(op.sql, str):
                    sql_content = op.sql.upper()
                elif isinstance(op.sql, (list, tuple)):
                    sql_content = " ".join(str(s).upper() for s in op.sql)

                destructive_keywords = ["DROP TABLE", "DROP COLUMN", "TRUNCATE"]
                for kw in destructive_keywords:
                    if kw in sql_content:
                        issues.append(
                            MigrationIssue(
                                app_label=app_label,
                                migration_name=migration_name,
                                severity="ERROR",
                                rule_id="RULE-MIG-05-DESTRUCTIVE-RAW-SQL",
                                message=(
                                    f"RunSQL operation contains destructive keyword '{kw}'. "
                                    "Destructive schema commands must not be executed during zero-downtime rolling updates."
                                ),
                            )
                        )
                        break

            # Rule 6: migrations.AlterField changing null from True to False without default
            elif isinstance(op, migrations.AlterField):
                field = op.field
                if not field.null and field.default is NOT_PROVIDED:
                    issues.append(
                        MigrationIssue(
                            app_label=app_label,
                            migration_name=migration_name,
                            severity="WARNING",
                            rule_id="RULE-MIG-06-ALTER-FIELD-NOT-NULL",
                            message=(
                                f"Altering field '{op.name}' on '{op.model_name}' to NOT NULL without a default. "
                                "Ensure table data has been 100% backfilled before applying this migration."
                            ),
                        )
                    )

        return issues
