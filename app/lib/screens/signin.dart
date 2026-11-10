import 'package:flutter/material.dart';

import '../api_client.dart';
import '../theme.dart';

class SignInPage extends StatefulWidget {
  const SignInPage({super.key, required this.client, required this.onDone, this.message});
  final ApiClient client;
  final VoidCallback onDone;
  final String? message;
  @override
  State<SignInPage> createState() => _SignInPageState();
}

class _SignInPageState extends State<SignInPage> {
  final email = TextEditingController(), password = TextEditingController(), org = TextEditingController(), code = TextEditingController();
  bool busy = false, askOrg = false, askCode = false;
  String? error;

  Future<void> _go() async {
    setState(() { busy = true; error = null; });
    try {
      final next = await widget.client.signIn(email.text.trim(), password.text, org: org.text.trim(), code: code.text.trim());
      if (next == 'code') {
        setState(() { askCode = true; busy = false; error = 'Enter the 6-digit code from your authenticator app.'; });
        return;
      }
      widget.onDone();
    } on ApiError catch (e) {
      setState(() { busy = false; if (e.code == 'choose_account') askOrg = true; error = e.message; });
    } catch (_) {
      setState(() { busy = false; error = 'We could not reach the server. Check your connection and try again.'; });
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Scaffold(
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: ListView(shrinkWrap: true, padding: const EdgeInsets.all(24), children: [
            Icon(Icons.center_focus_strong, color: t.brand, size: 40),
            const SizedBox(height: 12),
            const Text('Welcome back', style: TextStyle(fontSize: 28, fontWeight: FontWeight.w700)),
            Text('Sign in to Jas iVision.', style: TextStyle(color: t.fg2)),
            if (widget.message != null) Padding(padding: const EdgeInsets.only(top: 10), child: Text(widget.message!, style: TextStyle(color: t.okInk, fontWeight: FontWeight.w600))),
            const SizedBox(height: 20),
            AutofillGroup(
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                TextField(controller: email, keyboardType: TextInputType.emailAddress, autofillHints: const [AutofillHints.username], decoration: const InputDecoration(labelText: 'Email', border: OutlineInputBorder())),
                const SizedBox(height: 14),
                TextField(controller: password, obscureText: true, autofillHints: const [AutofillHints.password], onSubmitted: (_) => busy ? null : _go(), decoration: const InputDecoration(labelText: 'Password', border: OutlineInputBorder())),
                if (askOrg) ...[
                  const SizedBox(height: 14),
                  TextField(controller: org, decoration: const InputDecoration(labelText: 'Organisation short name', helperText: 'Your email is used in more than one organisation. Ask your administrator for the short name.', border: OutlineInputBorder())),
                ],
                if (askCode) ...[
                  const SizedBox(height: 14),
                  TextField(controller: code, keyboardType: TextInputType.number, decoration: const InputDecoration(labelText: 'Authenticator code', border: OutlineInputBorder())),
                ],
              ]),
            ),
            if (error != null) Padding(padding: const EdgeInsets.only(top: 12), child: Text(error!, style: TextStyle(color: t.crit, fontWeight: FontWeight.w600))),
            const SizedBox(height: 18),
            FilledButton(onPressed: busy ? null : _go, child: Text(busy ? 'Signing in…' : 'Sign in')),
            const SizedBox(height: 14),
            Text('Forgotten your password? Ask your administrator or Jas iVision support for a new link.', style: TextStyle(color: t.fg2, fontSize: 13)),
          ]),
        ),
      ),
    );
  }
}
