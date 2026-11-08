#!/usr/bin/env bash
# Blue-Green Instant Traffic Switch & Rollback Script
set -e

COLOR="$1"
NAMESPACE="${2:-production}"

if [ "$COLOR" != "blue" ] && [ "$COLOR" != "green" ]; then
  echo "Usage: ./switch_traffic.sh [blue|green] [namespace]"
  echo "Example to switch to Green: ./switch_traffic.sh green production"
  echo "Example to rollback to Blue: ./switch_traffic.sh blue production"
  exit 1
fi

echo "Switching live traffic pointer to: $COLOR (Namespace: $NAMESPACE)..."
kubectl patch service fuel-route-optimizer -n "$NAMESPACE" -p "{\"spec\":{\"selector\":{\"version\":\"$COLOR\"}}}"
echo "✓ Traffic successfully switched to $COLOR in < 1 second!"
