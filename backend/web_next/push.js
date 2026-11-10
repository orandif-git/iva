// Browser push: asks permission and gets this browser's push address from Firebase (Firebase Cloud Messaging). Plain script, no CDN: the Firebase files are in /firebase.
// The Firebase web config below is public by design (it identifies the project, it does not unlock it). See docs/PUSH.md.
(function () {
  var CONFIG = {
    apiKey: "AIzaSyDPZX2S4t4etPzJZ6NYz3cnZsTGLBqvbs8",
    authDomain: "ivision-ade8d.firebaseapp.com",
    projectId: "ivision-ade8d",
    storageBucket: "ivision-ade8d.firebasestorage.app",
    messagingSenderId: "860950377806",
    appId: "1:860950377806:web:c5d08595ee8307b142da1f"
  };
  var VAPID = "BBBMIqJ7PrR5u-v_DU2gZPWKyDfSh8Sa02F8kEabk9Ouy11C3wgb3XvCDUyi1hI_-Lmyf7iYAjVHqJAuQpnYyZA";
  var messaging = null;

  function supported() {
    return !!(window.firebase && window.isSecureContext && "serviceWorker" in navigator && "Notification" in window && "PushManager" in window);
  }
  function permission() { return supported() ? Notification.permission : "unsupported"; }

  // Wait until the worker that will show notifications is running. (Not serviceWorker.ready: that waits for a worker controlling this page, and this one has its own scope.)
  function active(reg) {
    var w = reg.active || reg.waiting || reg.installing;
    if (!w || w.state === "activated") return Promise.resolve();
    return new Promise(function (resolve) {
      var done = function () { if (w.state === "activated") resolve(); };
      w.addEventListener("statechange", done);
      setTimeout(resolve, 10000);
    });
  }

  // The address of this browser, or null. With ask=true the browser's permission box may open (only call that from a tap).
  async function token(ask) {
    if (!supported()) return null;
    if (Notification.permission === "default" && ask) await Notification.requestPermission();
    if (Notification.permission !== "granted") return null;
    // Its own small scope inside /next/, so it never replaces the app's own service worker.
    var reg = await navigator.serviceWorker.register("firebase-messaging-sw.js", { scope: "firebase-push/" });
    await active(reg);
    if (!messaging) {
      if (!firebase.apps.length) firebase.initializeApp(CONFIG);
      messaging = firebase.messaging();
    }
    return await messaging.getToken({ vapidKey: VAPID, serviceWorkerRegistration: reg });
  }

  window.giPush = { supported: supported, permission: permission, token: token };
})();
