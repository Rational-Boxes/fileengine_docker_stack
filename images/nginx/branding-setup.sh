#!/usr/bin/env bash
# Renders the SPA's white-label overrides from the environment.
#
# The SPA image is built once and deployed everywhere, so branding cannot be a
# build-time constant without a separate image per customer — which is the thing
# white-labelling exists to avoid. Instead the SPA fetches /branding.json at
# startup, and this writes that file from whatever the deployment set.
#
# Nothing set means no file is written, and the SPA runs as stock FileEngine.
set -e

OUT=/usr/share/nginx/html/branding.json

# Every knob, and the palette variable each colour drives.
NAME="${BRAND_APP_NAME:-}"
ICON="${BRAND_ICON_URL:-}"
TITLE="${BRAND_TITLE:-}"

LIGHT_KEYS=(fg muted border bg card primary primaryHover danger success)
LIGHT_VALS=("$BRAND_LIGHT_FG" "$BRAND_LIGHT_MUTED" "$BRAND_LIGHT_BORDER" \
            "$BRAND_LIGHT_BG" "$BRAND_LIGHT_CARD" "$BRAND_LIGHT_PRIMARY" \
            "$BRAND_LIGHT_PRIMARY_HOVER" "$BRAND_LIGHT_DANGER" "$BRAND_LIGHT_SUCCESS")
DARK_VALS=("$BRAND_DARK_FG" "$BRAND_DARK_MUTED" "$BRAND_DARK_BORDER" \
           "$BRAND_DARK_BG" "$BRAND_DARK_CARD" "$BRAND_DARK_PRIMARY" \
           "$BRAND_DARK_PRIMARY_HOVER" "$BRAND_DARK_DANGER" "$BRAND_DARK_SUCCESS")

# A stale file from a previous run would keep branding a deployment has just
# removed, so the absent case is a delete and not a no-op.
#
# Concatenated element by element, not "${arr[*]}": that joins on IFS, so nine
# empty values produce eight spaces and the emptiness test never fires.
ANY="$NAME$ICON$TITLE"
for _v in "${LIGHT_VALS[@]}" "${DARK_VALS[@]}"; do ANY="$ANY$_v"; done

if [ -z "$ANY" ]; then
  rm -f "$OUT"
  echo "branding: none configured; serving stock FileEngine"
  exit 0
fi

# JSON string escaping. Values come from a deployment's own environment, but a
# stray quote would produce a malformed file the SPA silently discards — which
# reads as "branding does not work" rather than "that value had a quote in it".
esc() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\t/\\t/g'; }

palette() {
  local -n vals=$1
  local out="" i
  for i in "${!LIGHT_KEYS[@]}"; do
    [ -n "${vals[$i]}" ] || continue
    [ -n "$out" ] && out="$out,"
    out="$out\"${LIGHT_KEYS[$i]}\":\"$(esc "${vals[$i]}")\""
  done
  printf '{%s}' "$out"
}

{
  printf '{'
  printf '"appName":"%s"' "$(esc "$NAME")"
  printf ',"iconUrl":"%s"' "$(esc "$ICON")"
  printf ',"title":"%s"' "$(esc "$TITLE")"
  printf ',"light":%s' "$(palette LIGHT_VALS)"
  printf ',"dark":%s' "$(palette DARK_VALS)"
  printf '}\n'
} > "$OUT"

echo "branding: wrote $OUT (appName='${NAME:-<default>}')"
