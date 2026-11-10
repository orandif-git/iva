# Push notifications: what is built and what you still need to do

**Built and tested:** the server keeps a list of each person's phones and browsers, and when an alert is raised it pushes it to every device of that account
(warning and critical alerts only, not during quiet hours). It sends through Firebase Cloud Messaging. A device is forgotten when it is gone, or when the
person signs out. WhatsApp is unchanged: push is an extra, never a replacement.

**Switched off until you do the steps below.** Nothing is sent and nothing breaks while it is off.

## Steps for you

1. **Create a Firebase project** at https://console.firebase.google.com (free). Name it for Jas iVision.
2. **Make a service-account key** for the server: Project settings, Service accounts, "Generate new private key". Save the JSON file on the server,
   for example `/www/wwwroot/iva/secrets/fcm.json`, readable only by the user that runs `gi-api` and `gi-worker`. Do not put it in git.
3. **Tell the server about it** (both `gi-api` and `gi-worker` read these; add them wherever the other `GI_` settings are):

   ```
   GI_FCM_PROJECT_ID=your-project-id
   GI_FCM_SERVICE_ACCOUNT_FILE=/www/wwwroot/iva/secrets/fcm.json
   ```

   Then restart both services.
4. **Run the new database migration** (adds the devices table): `backend/migrations/013_push_devices.sql`.
5. **The app side is built** (Firebase project `ivision-ade8d`): browsers and Android phones can register. The values in `app/web/push.js`, `app/web/firebase-messaging-sw.js`,
   `app/lib/firebase_push_io.dart` and `app/android/app/google-services.json` are public project identifiers, not secrets. The Firebase scripts the browser needs are kept
   on our own server (`app/web/firebase/`), and the page's security policy allows only the three Google addresses a browser needs to get its push address.
6. **Turn it on, per device:** sign in, open **More → Settings → Push notifications → Turn on**, and allow notifications in the box that appears. (A browser only allows that
   box after a tap, which is why it is a button and not automatic.) A device that already allowed it registers by itself after sign-in. To test, make an alert happen
   (for example switch off a camera) and wait for it; warning and critical alerts are pushed, not "info" ones.
7. **iPhone** is not set up: it needs an Apple Developer account, then an APNs key uploaded in the Firebase console (Project settings, Cloud Messaging), and the iPhone Firebase config.

Everything else (asking the server whether push is on, giving it the token, removing it on sign-out) is in the app and tested.

**Not yet checked on a real device:** a real notification arriving. The pieces are tested one by one (server, registration, the browser worker starting under the security policy), but
the first end-to-end test is yours, on a phone and a browser.
