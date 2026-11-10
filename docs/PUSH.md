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
5. **The app side (needs the Firebase app config):** register the Android, iOS and web apps in the Firebase console, then run `flutterfire configure` in `app/`
   and add `firebase_core` and `firebase_messaging`. Write one small class that implements `PushService` (in `app/lib/push.dart`) and returns the device
   token from `FirebaseMessaging.instance.getToken()`, then pass it to `IVisionApp(push: ...)` in `main.dart`. For iPhones you also upload an Apple push key
   (APNs) in the Firebase console; that needs an Apple developer account.

Everything else (asking the server whether push is on, giving it the token after sign-in, removing it on sign-out) is already in the app and tested.
