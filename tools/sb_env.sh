#!/usr/bin/env bash
# Looks up the Supabase project's URL and keys from a Supabase access token, so they never have to be copied by hand.
# Needs SUPABASE_ACCESS_TOKEN; SUPABASE_PROJECT_REF is optional when the token sees a single project.
# Writes SUPABASE_PROJECT_REF, SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY to $GITHUB_ENV (keys masked in logs).
set -euo pipefail
API=https://api.supabase.com/v1
: "${SUPABASE_ACCESS_TOKEN:?Add the SUPABASE_ACCESS_TOKEN secret}"

# sb_get PATH -> prints the body; on an error prints the status and Supabase's message (never the token) and exits.
sb_get() {
  local out code
  out=$(curl -sS -w '\n%{http_code}' -H "Authorization: Bearer $SUPABASE_ACCESS_TOKEN" "$API$1")
  code=${out##*$'\n'}; out=${out%$'\n'*}
  if [ "$code" -ge 400 ]; then
    echo "::error::Supabase said $code for $2: $(echo "$out" | head -c 400)"
    case "$code" in
      401) echo "The access token was rejected: it may be revoked, expired or mistyped. Make a new one and update the SUPABASE_ACCESS_TOKEN secret.";;
      403) echo "The access token isn't allowed to do this. Give it access to the bet-desk project with Project read, Database and Application services read+write, or use a legacy token.";;
    esac
    exit 1
  fi
  printf '%s' "$out"
}

REF="${SUPABASE_PROJECT_REF:-}"
if [ -z "$REF" ]; then
  PROJECTS=$(sb_get /projects "listing your projects")
  echo "Projects this token can see: $(echo "$PROJECTS" | jq -r '[.[]|.name+" ("+.id+")"]|join(", ")')"
  # the project named bet-desk, else the only project
  REF=$(echo "$PROJECTS" | jq -r '([.[]|select((.name|ascii_downcase)=="bet-desk")] | if length==1 then .[0].id else empty end) // empty')
  [ -n "$REF" ] || REF=$(echo "$PROJECTS" | jq -r 'if length==1 then .[0].id else empty end')
  [ -n "$REF" ] || { echo "::error::Couldn't tell which project is Bet Desk; add a SUPABASE_PROJECT_REF secret with its id."; exit 1; }
fi
KEYS=$(sb_get "/projects/$REF/api-keys?reveal=true" "reading the project's API keys")
ANON=$(echo "$KEYS" | jq -r '[.[]|select(.name=="anon")][0].api_key // empty')
SERVICE=$(echo "$KEYS" | jq -r '[.[]|select(.name=="service_role")][0].api_key // empty')
if [ -z "$ANON" ] || [ -z "$SERVICE" ]; then
  echo "::error::Couldn't find the anon / service_role keys. Key names found: $(echo "$KEYS" | jq -r '[.[].name]|join(", ")')"
  exit 1
fi
echo "::add-mask::$SERVICE"
{
  echo "SUPABASE_PROJECT_REF=$REF"
  echo "SUPABASE_URL=https://$REF.supabase.co"
  echo "SUPABASE_ANON_KEY=$ANON"
  echo "SUPABASE_SERVICE_KEY=$SERVICE"
} >> "$GITHUB_ENV"
echo "Supabase project: $REF"
