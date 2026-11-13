import 'package:flutter/material.dart';

import '../api_client.dart';
import '../theme.dart';
import '../widgets/brand.dart';

/// The page behind the link in an invitation: the person chooses their own password. The link's token is used once, and the address is cleaned afterwards.
class SetPasswordPage extends StatefulWidget {
  const SetPasswordPage({super.key, required this.client, required this.token, required this.onDone});
  final ApiClient client;
  final String token;
  final VoidCallback onDone;
  @override
  State<SetPasswordPage> createState() => _SetPasswordPageState();
}

class _SetPasswordPageState extends State<SetPasswordPage> {
  final a = TextEditingController(), b = TextEditingController();
  bool busy = false, hide = true;
  String? error;

  Future<void> _save() async {
    if (a.text.length < 10) return setState(() => error = 'Use at least 10 characters. A few words together is easy to remember and hard to guess.');
    if (a.text != b.text) return setState(() => error = 'The two passwords are not the same.');
    setState(() { busy = true; error = null; });
    try {
      await widget.client.setPassword(widget.token, a.text);
      widget.onDone();
    } on ApiError catch (e) {
      setState(() { busy = false; error = e.message; });
    } catch (_) {
      setState(() { busy = false; error = 'We could not reach the server. Check your connection and try again.'; });
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 440),
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                const Align(alignment: Alignment.centerLeft, child: BrandLogo(style: LogoStyle.compact, height: 48)),
                const SizedBox(height: 28),
                Text('Choose your password', style: Theme.of(context).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w700, letterSpacing: -.4)),
                const SizedBox(height: 6),
                Text('You will use it, with your email, to sign in.', style: TextStyle(color: t.fg2)),
                const SizedBox(height: 22),
                if (widget.token.isEmpty)
                  Container(
                    padding: const EdgeInsets.all(14),
                    decoration: BoxDecoration(color: t.warnSoft, borderRadius: BorderRadius.circular(Radii.control)),
                    child: Text('This link is not complete. Open the full link you were sent, or ask for a new one.', style: TextStyle(color: t.warnInk, fontWeight: FontWeight.w600)),
                  )
                else ...[
                  TextField(
                    controller: a,
                    obscureText: hide,
                    autofillHints: const [AutofillHints.newPassword],
                    decoration: InputDecoration(
                      labelText: 'New password',
                      helperText: 'At least 10 characters',
                      suffixIcon: IconButton(tooltip: hide ? 'Show password' : 'Hide password', icon: Icon(hide ? Icons.visibility_outlined : Icons.visibility_off_outlined), onPressed: () => setState(() => hide = !hide)),
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(controller: b, obscureText: hide, onSubmitted: (_) => busy ? null : _save(), decoration: const InputDecoration(labelText: 'Type it again')),
                  if (error != null) Padding(padding: const EdgeInsets.only(top: 12), child: Text(error!, style: TextStyle(color: t.crit, fontWeight: FontWeight.w600))),
                  const SizedBox(height: 18),
                  FilledButton(onPressed: busy ? null : _save, child: Text(busy ? 'Saving…' : 'Save password')),
                ],
              ]),
            ),
          ),
        ),
      ),
    );
  }
}
