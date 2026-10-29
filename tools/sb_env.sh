#!/usr/bin/env bash
# Looks up the Supabase project's URL and keys from a Supabase access token, so they never have to be copied by hand.
# Needs SUPABASE_ACCESS_TOKEN; SUPABASE_PROJECT_REF is optional when the account has a single project.
# Writes SUPABASE_PROJECT_REF, SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY to $GITHUB_ENV (keys masked in logs).
set -euo pipefail
API=https://api.supabase.com/v1
H=(-sS -f -H "Authorization: Bearer ${SUPABASE_ACCESS_TOKEN:?Add the SUPABASE_ACCESS_TOKEN secret}")
REF="${SUPABASE_PROJECT_REF:-}"
if [ -z "$REF" ]; then
  REF=$(curl "${H[@]}" "$API/projects" | jq -r 'if length==1 then .[0].id else empty end')
  [ -n "$REF" ] || { echo "Your Supabase account has more than one project: add the SUPABASE_PROJECT_REF secret."; exit 1; }
fi
KEYS=$(curl "${H[@]}" "$API/projects/$REF/api-keys?reveal=true")
ANON=$(echo "$KEYS" | jq -r '[.[]|select(.name=="anon")][0].api_key')
SERVICE=$(echo "$KEYS" | jq -r '[.[]|select(.name=="service_role")][0].api_key')
[ -n "$ANON" ] && [ "$ANON" != null ] && [ -n "$SERVICE" ] && [ "$SERVICE" != null ] || { echo "Could not read the project's API keys."; exit 1; }
echo "::add-mask::$SERVICE"
{
  echo "SUPABASE_PROJECT_REF=$REF"
  echo "SUPABASE_URL=https://$REF.supabase.co"
  echo "SUPABASE_ANON_KEY=$ANON"
  echo "SUPABASE_SERVICE_KEY=$SERVICE"
} >> "$GITHUB_ENV"
echo "Supabase project: $REF"
