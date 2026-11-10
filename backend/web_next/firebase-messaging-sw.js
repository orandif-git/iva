// Shows a notification when a push arrives while the app is not open. Firebase's own code does the showing; a tap opens the app (the link the server sends).
importScripts("firebase/firebase-app-compat.js", "firebase/firebase-messaging-compat.js");
firebase.initializeApp({
  apiKey: "AIzaSyDPZX2S4t4etPzJZ6NYz3cnZsTGLBqvbs8",
  authDomain: "ivision-ade8d.firebaseapp.com",
  projectId: "ivision-ade8d",
  storageBucket: "ivision-ade8d.firebasestorage.app",
  messagingSenderId: "860950377806",
  appId: "1:860950377806:web:c5d08595ee8307b142da1f"
});
firebase.messaging();
