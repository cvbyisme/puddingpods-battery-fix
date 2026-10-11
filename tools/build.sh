#!/usr/bin/env bash
# Rebuild PuddingPods with the GAIA framer fix.
#
# Inputs (env, all optional except BASE_APK):
#   BASE_APK       official PuddingPods 3.47 apk           (default ./base.apk)
#   BAKSMALI_JAR   baksmali-2.5.2.jar                      (default ./baksmali.jar)
#   SMALI_JAR      smali-2.5.2.jar                         (default ./smali.jar)
#   KEYSTORE       signing keystore                        (default ./keystore/puddingfix.jks)
#   KS_ALIAS/KS_PASS/KEY_PASS                             (default puddingfix/android/android)
#   ZIPALIGN/APKSIGNER  build-tools binaries               (default: from PATH)
#   OUT_DIR        output directory                        (default ./out)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE_APK="${BASE_APK:-$ROOT/base.apk}"
BAKSMALI_JAR="${BAKSMALI_JAR:-$ROOT/baksmali.jar}"
SMALI_JAR="${SMALI_JAR:-$ROOT/smali.jar}"
KEYSTORE="${KEYSTORE:-$ROOT/keystore/puddingfix.jks}"
KS_ALIAS="${KS_ALIAS:-puddingfix}"
KS_PASS="${KS_PASS:-android}"
KEY_PASS="${KEY_PASS:-android}"
ZIPALIGN="${ZIPALIGN:-zipalign}"
APKSIGNER="${APKSIGNER:-apksigner}"
OUT_DIR="${OUT_DIR:-$ROOT/out}"
WORK="$ROOT/build"

for f in "$BASE_APK" "$BAKSMALI_JAR" "$SMALI_JAR" "$KEYSTORE"; do
  [ -f "$f" ] || { echo "missing input: $f" >&2; exit 1; }
done
for t in java zip "$ZIPALIGN" "$APKSIGNER"; do
  command -v "$t" >/dev/null || { echo "missing tool: $t" >&2; exit 1; }
done

rm -rf "$WORK" "$OUT_DIR"
mkdir -p "$WORK/base" "$WORK/apk" "$OUT_DIR"

echo "== 1/6 extract classes.dex"
unzip -o -q "$BASE_APK" classes.dex -d "$WORK/base"

echo "== 2/6 baksmali"
java -jar "$BAKSMALI_JAR" d "$WORK/base/classes.dex" -o "$WORK/smali"

echo "== 3/6 patch framer (Llc1;->b([B))"
python3 "$ROOT/tools/patch_smali.py" "$WORK/smali"

echo "== 4/6 smali assemble (api 35 -> dex 039)"
java -jar "$SMALI_JAR" a -a 35 "$WORK/smali" -o "$WORK/classes.dex"
head -c 8 "$WORK/classes.dex" | grep -q 'dex' || { echo "assembled dex looks wrong" >&2; exit 1; }
# sanity: the fix must be present in the assembled source
grep -q 'add-int/lit8 v2, v2, 0x4' "$WORK/smali/lc1.smali" || { echo "patch marker lost" >&2; exit 1; }

echo "== 5/6 repack apk (only classes.dex changes)"
cp "$BASE_APK" "$WORK/patched.apk"
chmod u+w "$WORK/patched.apk"
zip -q -d "$WORK/patched.apk" classes.dex
cp "$WORK/classes.dex" "$WORK/apk/classes.dex"
(cd "$WORK/apk" && zip -q -X -0 ../patched.apk classes.dex)
"$ZIPALIGN" -f -p 4 "$WORK/patched.apk" "$WORK/aligned.apk"

echo "== 6/6 sign"
OUT="$OUT_DIR/PuddingPods-3.47-batteryfix.apk"
"$APKSIGNER" sign --ks "$KEYSTORE" --ks-key-alias "$KS_ALIAS" \
  --ks-pass "pass:$KS_PASS" --key-pass "pass:$KEY_PASS" \
  --out "$OUT" "$WORK/aligned.apk"
"$APKSIGNER" verify --print-certs "$OUT" | head -5

echo
echo "OK: $OUT"
ls -l "$OUT"
