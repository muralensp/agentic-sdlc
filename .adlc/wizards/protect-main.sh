#!/usr/bin/env bash
# protect-main.sh: create the GitHub remote for this repo and protect `main`.
#
# These steps need a person (GitHub account, admin rights), so they are a wizard, not prose.
# Every step prints what it will do, asks first, and checks the result. Safe to re-run.
#
# Usage: .adlc/wizards/protect-main.sh [--dry-run] [--yes] [--repo OWNER/NAME] [--public]
#   --dry-run  print the plan and make no change (read-only gh calls only)
#   --yes      do not ask before each step (still prints each step)
#   --repo     OWNER/NAME of the remote (default: <your login>/<this folder's name>)
#   --public   create the repo as public (default private)
#
# Environment: ADLC_GH overrides the gh command (used by the tests).

set -u

GH="${ADLC_GH:-gh}"
CHECK="${ADLC_REQUIRED_CHECK:-CI passed}"
BRANCH="main"
DRY=0
YES=0
REPO=""
VIS="private"

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --yes) YES=1 ;;
    --public) VIS="public" ;;
    --repo) shift; REPO="${1:-}" ;;
    -h | --help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

step() { printf '\n== %s\n' "$1"; }
say() { printf '   %s\n' "$1"; }
die() { printf 'STOP: %s\n' "$1" >&2; exit 1; }

# confirm "what we are about to do"; returns 1 when the person declines.
confirm() {
  say "will do: $1"
  [ "$DRY" -eq 1 ] && { say "(dry run, skipped)"; return 1; }
  [ "$YES" -eq 1 ] && return 0
  read -r -p "   proceed? [y/N] " ans
  [ "$ans" = "y" ] || [ "$ans" = "Y" ]
}

step "1. Preflight"
"$GH" auth status >/dev/null 2>&1 || die "gh is not logged in. Run: gh auth login"
git rev-parse --git-dir >/dev/null 2>&1 || die "run this inside the ADLC git repository"
git rev-parse --verify "$BRANCH" >/dev/null 2>&1 || die "no local '$BRANCH' branch with a commit"
LOGIN="$("$GH" api user --jq .login 2>/dev/null)" || die "could not read your GitHub login"
[ -n "$REPO" ] || REPO="$LOGIN/$(basename "$(git rev-parse --show-toplevel)")"
say "account: $LOGIN   repo: $REPO ($VIS)   required check: $CHECK"

step "2. Remote repository"
if "$GH" repo view "$REPO" >/dev/null 2>&1; then
  say "already exists, nothing to create"
else
  confirm "gh repo create $REPO --$VIS (no push yet)" \
    && { "$GH" repo create "$REPO" "--$VIS" >/dev/null || die "repo create failed"; say "created"; }
fi

step "3. origin and first push"
URL="https://github.com/$REPO.git"
if [ -z "$(git remote get-url origin 2>/dev/null)" ]; then
  confirm "git remote add origin $URL" && git remote add origin "$URL"
else
  say "origin is $(git remote get-url origin)"
fi
if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  say "origin already has $BRANCH"
else
  # Must happen before protection: afterwards a direct push is, by design, rejected.
  confirm "git push -u origin $BRANCH" && { git push -u origin "$BRANCH" || die "push failed"; }
fi

step "4. Protect $BRANCH"
BODY=$(cat <<JSON
{
  "required_status_checks": {"strict": true, "contexts": ["$CHECK"]},
  "enforce_admins": true,
  "required_pull_request_reviews": {"required_approving_review_count": 0},
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
)
say "rules: pull request required, check '$CHECK' must pass and be up to date,"
say "       applies to administrators, no force push, no deletion"
if confirm "PUT repos/$REPO/branches/$BRANCH/protection"; then
  printf '%s' "$BODY" | "$GH" api -X PUT "repos/$REPO/branches/$BRANCH/protection" --input - >/dev/null \
    || die "GitHub refused. Private repos need GitHub Pro/Team for branch protection; rerun with --public or upgrade."
fi

step "5. Verify the rules are in force"
P="repos/$REPO/branches/$BRANCH/protection"
ok=1
[ "$("$GH" api "$P" --jq .enforce_admins.enabled 2>/dev/null)" = "true" ] || { say "FAIL admins not enforced"; ok=0; }
"$GH" api "$P" --jq '.required_status_checks.contexts[]' 2>/dev/null | grep -qx "$CHECK" \
  || { say "FAIL check '$CHECK' not required"; ok=0; }
[ "$("$GH" api "$P" --jq .allow_force_pushes.enabled 2>/dev/null)" = "false" ] || { say "FAIL force push allowed"; ok=0; }
[ "$("$GH" api "$P" --jq '.required_pull_request_reviews|type' 2>/dev/null)" = "object" ] || { say "FAIL pull request not required"; ok=0; }
if [ "$ok" -eq 1 ]; then say "OK all four rules present"; else say "rules not (yet) in place"; fi

step "6. Evidence: a direct push to $BRANCH is rejected"
if [ "$ok" -ne 1 ]; then
  say "skipped: protection is not verified, so a probe push could really land"
elif confirm "push an empty probe commit straight to $BRANCH (must be rejected; local state untouched)"; then
  PROBE="$(git commit-tree "$BRANCH^{tree}" -p "$BRANCH" -m 'probe: direct push must be rejected')"
  if OUT="$(git push origin "$PROBE:refs/heads/$BRANCH" 2>&1)"; then
    say "FAIL the push was ACCEPTED. $BRANCH is not protected. Fix this before continuing."
    exit 1
  fi
  say "rejected, as required. GitHub said:"
  printf '%s\n' "$OUT" | sed 's/^/     /'
fi

printf '\nDone.\n'
