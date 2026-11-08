"""CLI management command to inspect and dynamically toggle feature flags."""
from typing import Any
from django.conf import settings
from django.core.management.base import BaseCommand
from apps.common.flags import flags


class Command(BaseCommand):
    help = "Inspect, toggle, or configure feature flags dynamically in cache/Redis."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--list",
            action="store_true",
            help="List all configured feature flags and their current values.",
        )
        parser.add_argument(
            "--get",
            type=str,
            help="Retrieve the current value of a specific feature flag.",
        )
        parser.add_argument(
            "--set",
            nargs=2,
            metavar=("FLAG_NAME", "VALUE"),
            help="Set feature flag value (e.g. --set NEW_SOLVER on, or --set CANARY_ROUTING 25%).",
        )
        parser.add_argument(
            "--delete",
            type=str,
            help="Delete dynamic override for a flag, reverting to settings or default.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options.get("list"):
            self._list_flags()
            return

        get_flag = options.get("get")
        if get_flag:
            val = flags.get_flag_value(get_flag, default="[NOT CONFIGURED / DISABLED]")
            self.stdout.write(f"{get_flag.upper()}: {val}")
            return

        set_flag_args = options.get("set")
        if set_flag_args:
            name, val = set_flag_args
            flags.set_flag(name, val)
            self.stdout.write(self.style.SUCCESS(f"Successfully set feature flag '{name.upper()}' to '{val}'."))
            return

        del_flag = options.get("delete")
        if del_flag:
            flags.delete_flag(del_flag)
            self.stdout.write(self.style.SUCCESS(f"Deleted dynamic override for feature flag '{del_flag.upper()}'."))
            return

        # Default action if no flags provided
        self._list_flags()

    def _list_flags(self) -> None:
        configured = getattr(settings, "FEATURE_FLAGS", {})
        self.stdout.write(self.style.MIGRATE_HEADING("Configured Feature Flags:"))
        if not configured:
            self.stdout.write("  No static feature flags configured in settings.")
        else:
            for k in sorted(configured.keys()):
                val = flags.get_flag_value(k)
                self.stdout.write(f"  * {k:<30} = {val}")
