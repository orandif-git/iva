# iPhone and Android apps

The customer app is one Flutter project (`app/`), so the phone apps are the same screens as the web app, built for Android and iPhone.

## What is already done

- Android and iPhone project folders (`app/android`, `app/ios`), the app name **Jas iVision**, the app icon (made from the logo mark), the internet permission.
- The app's identity: **`in.jas.ivision`** (Android application id and iPhone bundle id). This is permanent once an app is on Google Play or the App Store.
  If you want a different one, change it **before** the first upload: `app/android/app/build.gradle.kts` (`applicationId`) and `app/ios/Runner.xcodeproj` (`PRODUCT_BUNDLE_IDENTIFIER`).
- Downloading a report works on phones through the share sheet (save to Files, send on WhatsApp, open in a spreadsheet app).
- A GitHub workflow that builds the apps for you: `.github/workflows/app-build.yml`.

## Trying the Android app (no accounts needed)

1. On GitHub: **Actions → Build phone apps → Run workflow**. Type your server address, for example `https://app.yourcompany.in`.
2. When it finishes, open the run and download the **android** file. Inside is `app-release.apk`.
3. Send the `.apk` to an Android phone, open it, and allow "install from this source". It signs in to your server like the web app does.

This build is signed with a test key, so it works for trying but cannot be uploaded to Google Play.

## Google Play (needs a Google Play developer account, one-time US$25)

1. Create the account at https://play.google.com/console and create the app **Jas iVision** with the id above.
2. Make an upload key once, on any computer with Java (keep the file and passwords safe; losing them means asking Google to reset it):

   ```
   keytool -genkey -v -keystore upload.jks -keyalg RSA -keysize 2048 -validity 10000 -alias upload
   ```
3. On GitHub: Settings → Secrets and variables → Actions, add four secrets: `ANDROID_KEYSTORE_BASE64` (the file as base64: `base64 -w0 upload.jks`),
   `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS` (`upload`), `ANDROID_KEY_PASSWORD`.
4. Run the workflow again. Upload `app-release.aab` to a Play Console **internal testing** track first, then promote it.
5. Google asks for a privacy policy address, screenshots and a short description for the listing. The app uses the internet only to talk to your server.

## The iPhone app (needs an Apple Developer account, US$99 a year)

1. Join the Apple Developer Program at https://developer.apple.com/programs.
2. The workflow's iPhone job only checks that the app compiles. A signed build for TestFlight and the App Store needs your Apple signing certificate and profile.
   The simplest route is a service such as Codemagic or Xcode Cloud connected to this repository, using `app/` as the project; or someone with a Mac runs
   `flutter build ipa --dart-define=API_BASE=https://your-domain` in `app/` and uploads with Xcode.
3. Store listing needs a privacy policy address, screenshots and a description, as for Google Play.

## Push notifications on phones

See `docs/PUSH.md`. Android already has its Firebase file (`app/android/app/google-services.json`) and turns push on from Settings. iPhone push needs an APNs key from your Apple account.

## Links into the app

The web address is read once, at start-up, by `app/lib/browser.dart`: `/set-password?token=…` opens the invitation screen and removes the one-time token from the address bar; a console hand-off opens the view-only session. Every other address is the app itself. The app deliberately does not use a routing package: the five places keep their own state and sub-screens open over them, so a URL cannot describe them without losing that state. If a public link is needed later (for example `/rules`), add it to `browser.dart` beside the invitation hand-off.

## Smooth live video

On phones the live view tries WebRTC first (go2rtc) and falls back to the picture stream when the server has no go2rtc, the camera cannot be reached, or the offer is refused. Nothing needs to be installed on the phone. The server side is in `API.md` (§4d): set `GI_GO2RTC_URL`, make go2rtc reachable from the phone network, and give it ICE candidates. Without it, live view keeps working with refreshed pictures.

## Building on your own computer

```
cd app
flutter build apk --release --dart-define=API_BASE=https://your-domain
```

needs the Android SDK; the iPhone build needs a Mac with Xcode.
