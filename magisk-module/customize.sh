TMP_FILE="$TMPDIR/{APK_NAME}"

chmod u+x "$MODPATH/uninstall.sh"
cp "$MODPATH/{APK_PATH}" "$TMP_FILE"

pm install -r "$TMP_FILE"
rm -f "$TMP_FILE"

pm grant "{PKG_NAME}" android.permission.READ_PHONE_STATE
