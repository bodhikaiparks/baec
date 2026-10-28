#!/usr/bin/env bash
# The operator's last gate before creating the public GitHub repository. It reads only this repository and changes
# nothing. Exit status 0 means every check passed.
#
#   ./scripts/check_publication_ready.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
failed=0

check() {  # check "<description>" <command...>
  local description="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "ok    $description"
  else
    echo "FAIL  $description"
    failed=1
  fi
}

no_match() { ! grep -q -- "$1" "$2"; }

authors_are_noreply() {
  local emails
  emails="$(git log --all --format='%ae%n%ce' | sort -u)"
  [ -n "$emails" ] || return 1
  while IFS= read -r email; do
    case "$email" in
      *@users.noreply.github.com) ;;
      *) return 1 ;;
    esac
  done <<<"$emails"
}

screenshots_present() {
  local name
  for name in 01-today 02-ai-intake 03-engine1 04-engine2 05-prepare 06-timeline; do
    [ -s "docs/images/$name.png" ] || return 1
  done
}

check "LICENSE is the Business Source License 1.1" grep -q "^Business Source License 1.1$" LICENSE
PLACEHOLDER="<LINKEDIN_PROFILE""_URL>"  # split so this script is not itself a placeholder instance
check "README has no contact placeholder" no_match "$PLACEHOLDER" README.md
check "LICENSE has no contact placeholder" no_match "$PLACEHOLDER" LICENSE
check "README no longer says screenshots are missing" no_match "Screenshots have not been captured yet" README.md
check "all six screenshots exist in docs/images/" screenshots_present
check "every commit author and committer uses a GitHub noreply address" authors_are_noreply
check "the working tree is clean" test -z "$(git status --porcelain)"

if [ "$failed" -ne 0 ]; then
  echo
  echo "Not ready to publish. See docs/PUBLIC_RELEASE.md, section \"Operator gate\"."
  exit 1
fi
echo
echo "Ready to publish."
