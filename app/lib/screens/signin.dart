import 'package:flutter/material.dart';

import '../api_client.dart';
import '../theme.dart';
import '../widgets/brand.dart';

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
  bool busy = false, askOrg = false, askCode = false, hide = true;
  String? error;

  void toggleHide() => setState(() => hide = !hide);

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
    final wide = MediaQuery.sizeOf(context).width >= 900;
    final form = _Form(this);
    return Scaffold(
      body: wide
          ? Row(children: [
              const Expanded(flex: 11, child: _HeroPanel()),
              Expanded(flex: 9, child: Center(child: SingleChildScrollView(padding: const EdgeInsets.all(40), child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 400), child: form)))),
            ])
          : SafeArea(
              child: Center(
                child: SingleChildScrollView(
                  padding: const EdgeInsets.all(24),
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 420),
                    child: Column(children: [
                      const SizedBox(height: 8),
                      const BrandLogo(style: LogoStyle.full, height: 96),
                      const SizedBox(height: 28),
                      form,
                    ]),
                  ),
                ),
              ),
            ),
    );
  }
}

class _Form extends StatelessWidget {
  const _Form(this.s);
  final _SignInPageState s;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      const Text('Welcome back', style: TextStyle(fontSize: 30, fontWeight: FontWeight.w700, letterSpacing: -.6)),
      const SizedBox(height: 4),
      Text('Sign in to see how your business is doing.', style: TextStyle(color: t.fg2, fontSize: 15)),
      if (s.widget.message != null) Padding(padding: const EdgeInsets.only(top: 12), child: Text(s.widget.message!, style: TextStyle(color: t.okInk, fontWeight: FontWeight.w600))),
      const SizedBox(height: 26),
      AutofillGroup(
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          TextField(controller: s.email, keyboardType: TextInputType.emailAddress, autofillHints: const [AutofillHints.username], decoration: const InputDecoration(labelText: 'Email', prefixIcon: Icon(Icons.mail_outline))),
          const SizedBox(height: 14),
          TextField(
              controller: s.password,
              obscureText: s.hide,
              autofillHints: const [AutofillHints.password],
              onSubmitted: (_) => s.busy ? null : s._go(),
              decoration: InputDecoration(labelText: 'Password', prefixIcon: const Icon(Icons.lock_outline), suffixIcon: IconButton(tooltip: s.hide ? 'Show password' : 'Hide password', icon: Icon(s.hide ? Icons.visibility_outlined : Icons.visibility_off_outlined), onPressed: s.toggleHide))),
          if (s.askOrg) ...[
            const SizedBox(height: 14),
            TextField(controller: s.org, decoration: const InputDecoration(labelText: 'Organisation short name', prefixIcon: Icon(Icons.business_outlined), helperText: 'Your email is used in more than one organisation. Ask your administrator for the short name.', helperMaxLines: 3)),
          ],
          if (s.askCode) ...[
            const SizedBox(height: 14),
            TextField(controller: s.code, keyboardType: TextInputType.number, decoration: const InputDecoration(labelText: 'Authenticator code', prefixIcon: Icon(Icons.pin_outlined))),
          ],
        ]),
      ),
      if (s.error != null)
        Padding(
            padding: const EdgeInsets.only(top: 14),
            child: Container(padding: const EdgeInsets.all(12), decoration: BoxDecoration(color: t.critSoft, borderRadius: BorderRadius.circular(12)), child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [Icon(Icons.error_outline, color: t.crit, size: 20), const SizedBox(width: 8), Expanded(child: Text(s.error!, style: TextStyle(color: t.crit, fontWeight: FontWeight.w600)))]))),
      const SizedBox(height: 22),
      FilledButton(style: FilledButton.styleFrom(minimumSize: const Size(44, 54)), onPressed: s.busy ? null : s._go, child: Text(s.busy ? 'Signing in…' : 'Sign in')),
      const SizedBox(height: 18),
      Text('Forgotten your password? Ask your administrator or Jas iVision support for a new link.', style: TextStyle(color: t.fg2, fontSize: 13), textAlign: TextAlign.center),
      const SizedBox(height: 26),
      Center(child: Text(tagline, style: TextStyle(color: t.fg2.withValues(alpha: .8), fontSize: 12.5, fontStyle: FontStyle.italic))),
    ]);
  }
}

/// The blue panel beside the sign-in form on a wide screen: the logo, what the product does, and the detection-frame motif.
class _HeroPanel extends StatelessWidget {
  const _HeroPanel();
  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: const BoxDecoration(gradient: LinearGradient(colors: [Color(0xFF07294D), Color(0xFF0A4A8F), Color(0xFF128BE0)], begin: Alignment.topLeft, end: Alignment.bottomRight)),
      child: Stack(children: [
        Positioned.fill(child: CustomPaint(painter: FramePainter(color: Colors.white.withValues(alpha: .16), inset: 36, arm: 56, width: 4))),
        const Positioned(right: -60, bottom: -40, child: LensGlow(size: 460)),
        Padding(
          padding: const EdgeInsets.all(72),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisAlignment: MainAxisAlignment.center, children: [
            const BrandLogo(style: LogoStyle.full, height: 150, onDark: true),
            const SizedBox(height: 56),
            const Text('See what is happening.\nKnow what matters.', style: TextStyle(color: Colors.white, fontSize: 38, fontWeight: FontWeight.w700, height: 1.15, letterSpacing: -1)),
            const SizedBox(height: 18),
            Text('Your cameras, counted and watched for you. Alerts reach you on WhatsApp the moment something needs you.', style: TextStyle(color: Colors.white.withValues(alpha: .82), fontSize: 16.5, height: 1.5)),
            const SizedBox(height: 34),
            for (final (icon, text) in const [(Icons.groups_outlined, 'People counting and busy hours'), (Icons.notifications_active_outlined, 'Alerts when a camera or connection has a problem'), (Icons.auto_awesome_outlined, 'Smart Alerts for queues, crowds and more')])
              Padding(
                padding: const EdgeInsets.only(bottom: 14),
                child: Row(children: [
                  Container(width: 38, height: 38, decoration: BoxDecoration(color: Colors.white.withValues(alpha: .14), borderRadius: BorderRadius.circular(12)), child: icon == Icons.auto_awesome_outlined ? const Center(child: AiSpark(size: 20, color: Colors.white)) : Icon(icon, color: Colors.white, size: 20)),
                  const SizedBox(width: 14),
                  Expanded(child: Text(text, style: const TextStyle(color: Colors.white, fontSize: 15.5, fontWeight: FontWeight.w500))),
                ]),
              ),
          ]),
        ),
      ]),
    );
  }
}
