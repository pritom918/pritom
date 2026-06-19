# Proguard rules for InstaOff app
# Add project specific Proguard rules here.

# Keep WebView and JavaScript interfaces safe
-keepattributes JavascriptInterface
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}
