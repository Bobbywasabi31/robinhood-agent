#!/usr/bin/env bash
# Open a (deduplicated) GitHub issue when the weekly S&P 500 universe drift
# check reports drift. Called from .github/workflows/universe-drift.yml.
#
# Usage: open-drift-issue.sh <drift-output-file>
# Env: GH_TOKEN (set by GitHub Actions); GH_REPO or the checked-out repo
#      supplies the target repository for `gh`.
set -euo pipefail

output_file="${1:?usage: open-drift-issue.sh <drift-output-file>}"
title="S&P 500 universe drift detected"

# Dedupe: if a drift issue is already open, don't file another one.
open_count="$(gh issue list --state open --search "universe drift detected in:title" --json number --jq 'length')"
if [ "${open_count:-0}" -gt 0 ]; then
  echo "A universe-drift issue is already open - skipping duplicate."
  exit 0
fi

# Ensure the label exists (no-op if it does).
gh label create universe-drift \
  --description "S&P 500 universe drift auto-filed by the weekly drift check" \
  --color "d73a4a" >/dev/null 2>&1 || true

diff_excerpt="$(head -c 4000 "$output_file")"
body="The weekly S&P 500 universe drift check found that \`universe.json\` no longer matches Wikipedia's constituent list.

To fix: run \`python3 scripts/check_universe_drift.py\` locally, update \`universe.json\` (and the Materials-sector flags) to match, then push. The issue stays open until the universe is fixed; the check re-files nothing while it is open.

<details>
<summary>Drift check output</summary>

\`\`\`
$diff_excerpt
\`\`\`

</details>
"

gh issue create --title "$title" --body "$body" --label universe-drift
