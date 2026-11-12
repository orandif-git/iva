import 'package:flutter/material.dart';

import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import 'brand.dart';

enum ThemeChoice { light, dark, auto }

/// The round profile button at the top right. It opens the menu: who you are, appearance (Light, Dark, Auto), change password, sign out.
class ProfileButton extends StatelessWidget {
  const ProfileButton({super.key, required this.me, required this.choice, required this.onChoice, required this.repo, required this.onSignOut});
  final Me? me;
  final ThemeChoice choice;
  final ValueChanged<ThemeChoice> onChoice;
  final Repository repo;
  final void Function(String? message) onSignOut;

  @override
  Widget build(BuildContext context) {
    final name = me?.name ?? '';
    return Semantics(
      button: true,
      label: 'Profile and settings',
      child: InkResponse(
        radius: 26,
        onTap: () => _open(context),
        child: Container(
          width: 40,
          height: 40,
          alignment: Alignment.center,
          decoration: const BoxDecoration(gradient: brandGradient, shape: BoxShape.circle),
          child: Text(name.isEmpty ? '·' : name[0].toUpperCase(), style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 16)),
        ),
      ),
    );
  }

  /// The menu drops down from the profile button, on a phone and on a desktop alike: it is short, it keeps the page in view, and the thumb reaches the top right easily.
  void _open(BuildContext context) {
    final media = MediaQuery.of(context);
    final wide = media.size.width >= 900;
    final top = wide ? 74.0 : media.padding.top + 62;
    final width = wide ? 340.0 : (media.size.width - 24).clamp(260.0, 340.0);
    Widget content(BuildContext ctx) => ProfileMenu(me: me, choice: choice, onChoice: onChoice, repo: repo, onSignOut: (m) { Navigator.of(ctx).pop(); onSignOut(m); });
    showGeneralDialog<void>(
      context: context,
      barrierDismissible: true,
      barrierLabel: 'Close menu',
      barrierColor: Colors.black.withValues(alpha: wide ? 0 : .18),
      transitionDuration: const Duration(milliseconds: 190),
      pageBuilder: (ctx, _, __) => Align(
        alignment: Alignment.topRight,
        child: Padding(
          padding: EdgeInsets.fromLTRB(12, top, wide ? 28 : 12, 12),
          child: Material(
            color: Colors.transparent,
            child: SizedBox(
              width: width,
              child: DecoratedBox(
                decoration: BoxDecoration(color: Tok.of(ctx).surface, borderRadius: BorderRadius.circular(Radii.card), border: Border.all(color: Tok.of(ctx).line), boxShadow: [BoxShadow(color: Tok.of(ctx).shadow.withValues(alpha: .35), blurRadius: 32, offset: const Offset(0, 12))]),
                child: ClipRRect(borderRadius: BorderRadius.circular(Radii.card), child: SingleChildScrollView(child: content(ctx))),
              ),
            ),
          ),
        ),
      ),
      transitionBuilder: (ctx, anim, _, child) {
        final curved = CurvedAnimation(parent: anim, curve: Curves.easeOutCubic, reverseCurve: Curves.easeInCubic);
        return FadeTransition(opacity: curved, child: ScaleTransition(alignment: Alignment.topRight, scale: Tween(begin: .88, end: 1.0).animate(curved), child: child));
      },
    );
  }
}

class ProfileMenu extends StatefulWidget {
  const ProfileMenu({super.key, required this.me, required this.choice, required this.onChoice, required this.repo, required this.onSignOut});
  final Me? me;
  final ThemeChoice choice;
  final ValueChanged<ThemeChoice> onChoice;
  final Repository repo;
  final void Function(String? message) onSignOut;
  @override
  State<ProfileMenu> createState() => _ProfileMenuState();
}

class _ProfileMenuState extends State<ProfileMenu> {
  late ThemeChoice choice = widget.choice;
  bool changing = false;
  String? note;

  String get role => switch (widget.me?.role) { 'admin' => 'Owner or manager', 'regular' => 'Team member', _ => '' };

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final m = widget.me;
    Widget row(IconData icon, String label, VoidCallback onTap, {Color? color, bool open = false}) => InkWell(
          onTap: onTap,
          child: Padding(padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14), child: Row(children: [Icon(icon, size: 22, color: color ?? t.fg2), const SizedBox(width: 14), Expanded(child: Text(label, style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15.5, color: color ?? t.fg))), if (color == null) Icon(open ? Icons.expand_less_rounded : Icons.chevron_right_rounded, color: t.fg2.withValues(alpha: .6))])),
        );
    return Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      if (m != null && m.name.isNotEmpty)
        Padding(
          padding: const EdgeInsets.fromLTRB(18, 14, 18, 14),
          child: Row(children: [
            Container(width: 48, height: 48, alignment: Alignment.center, decoration: const BoxDecoration(gradient: brandGradient, shape: BoxShape.circle), child: Text(m.name[0].toUpperCase(), style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 19))),
            const SizedBox(width: 14),
            Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(m.name, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 17)), Text(m.business, style: TextStyle(color: t.fg2, fontSize: 13.5)), if (role.isNotEmpty) Text(role, style: TextStyle(color: t.fg2, fontSize: 12.5))])),
          ]),
        ),
      Divider(height: 1, color: t.line),
      Padding(
        padding: const EdgeInsets.fromLTRB(18, 14, 18, 14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('APPEARANCE', style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, letterSpacing: .6, color: t.fg2)),
          const SizedBox(height: 8),
          SizedBox(
            width: double.infinity,
            child: SegmentedButton<ThemeChoice>(
              showSelectedIcon: false,
              segments: const [ButtonSegment(value: ThemeChoice.light, label: Text('Light'), icon: Icon(Icons.light_mode_outlined, size: 18)), ButtonSegment(value: ThemeChoice.dark, label: Text('Dark'), icon: Icon(Icons.dark_mode_outlined, size: 18)), ButtonSegment(value: ThemeChoice.auto, label: Text('Auto'), icon: Icon(Icons.brightness_auto_outlined, size: 18))],
              selected: {choice},
              onSelectionChanged: (v) { setState(() => choice = v.first); widget.onChoice(v.first); },
            ),
          ),
          const SizedBox(height: 6),
          Text('Auto follows your phone or computer.', style: TextStyle(color: t.fg2, fontSize: 12.5)),
        ]),
      ),
      Divider(height: 1, color: t.line),
      Divider(height: 1, indent: 18, color: t.line),
      row(Icons.lock_outline_rounded, 'Change password', () => setState(() { changing = !changing; note = null; }), open: changing),
      AnimatedSize(
        duration: const Duration(milliseconds: 220),
        curve: Curves.easeOut,
        alignment: Alignment.topCenter,
        child: changing
            ? ChangePasswordPanel(repo: widget.repo, onCancel: () => setState(() => changing = false), onDone: (others) => setState(() { changing = false; note = others ? 'Password changed. Other devices were signed out.' : 'Password changed.'; }))
            : (note == null ? const SizedBox(width: double.infinity) : Padding(padding: const EdgeInsets.fromLTRB(18, 0, 18, 12), child: Row(children: [Icon(Icons.check_circle_rounded, color: t.okInk, size: 18), const SizedBox(width: 8), Expanded(child: Text(note!, style: TextStyle(color: t.okInk, fontWeight: FontWeight.w600, fontSize: 13.5)))]))),
      ),
      Divider(height: 1, indent: 18, color: t.line),
      row(Icons.logout_rounded, 'Sign out', () => widget.onSignOut(null), color: t.crit),
      const SizedBox(height: 6),
    ]);
  }
}

/// Changing the password, right inside the menu: no pop-up. This device stays signed in; the checkbox decides whether every other device is signed out.
class ChangePasswordPanel extends StatefulWidget {
  const ChangePasswordPanel({super.key, required this.repo, required this.onCancel, required this.onDone});
  final Repository repo;
  final VoidCallback onCancel;
  final void Function(bool signedOutOthers) onDone;
  @override
  State<ChangePasswordPanel> createState() => _ChangePasswordPanelState();
}

class _ChangePasswordPanelState extends State<ChangePasswordPanel> {
  final cur = TextEditingController(), a = TextEditingController(), b = TextEditingController();
  bool busy = false, others = true, show = false;
  String? error;

  @override
  void initState() {
    super.initState();
    // On a short phone the form opens below the fold: bring it into view once it has grown.
    Future.delayed(const Duration(milliseconds: 240), () { if (mounted) Scrollable.ensureVisible(context, duration: const Duration(milliseconds: 200), curve: Curves.easeOut, alignment: 1); });
  }

  Future<void> _save() async {
    if (cur.text.isEmpty) return setState(() => error = 'Enter your current password.');
    if (a.text.length < 10) return setState(() => error = 'Use at least 10 characters. A few words together is easy to remember and hard to guess.');
    if (a.text != b.text) return setState(() => error = 'The two new passwords are not the same.');
    setState(() { busy = true; error = null; });
    try {
      await widget.repo.changePassword(cur.text, a.text, signOutOthers: others);
      if (mounted) widget.onDone(others);
    } on ApiError catch (e) {
      setState(() { busy = false; error = e.message; });
    } catch (_) {
      setState(() { busy = false; error = 'We could not reach the server.'; });
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    InputDecoration dec(String label, {String? helper}) => InputDecoration(labelText: label, helperText: helper, isDense: true, suffixIcon: null);
    return Padding(
      padding: const EdgeInsets.fromLTRB(18, 4, 18, 16),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        TextField(controller: cur, obscureText: !show, autofillHints: const [AutofillHints.password], decoration: dec('Current password')),
        const SizedBox(height: 10),
        TextField(controller: a, obscureText: !show, autofillHints: const [AutofillHints.newPassword], decoration: dec('New password', helper: 'At least 10 characters')),
        const SizedBox(height: 10),
        TextField(controller: b, obscureText: !show, onSubmitted: (_) => busy ? null : _save(), decoration: dec('Confirm new password')),
        Align(alignment: Alignment.centerLeft, child: TextButton.icon(style: TextButton.styleFrom(minimumSize: const Size(44, 36), padding: const EdgeInsets.symmetric(horizontal: 4)), onPressed: () => setState(() => show = !show), icon: Icon(show ? Icons.visibility_off_outlined : Icons.visibility_outlined, size: 18), label: Text(show ? 'Hide passwords' : 'Show passwords'))),
        InkWell(
          borderRadius: BorderRadius.circular(Radii.chip),
          onTap: () => setState(() => others = !others),
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Checkbox(value: others, onChanged: (v) => setState(() => others = v ?? true), visualDensity: VisualDensity.compact),
              Expanded(child: Padding(padding: const EdgeInsets.only(top: 6), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                const Text('Sign out of all other devices', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
                Text('This device stays signed in. Others sign in again with the new password.', style: TextStyle(color: t.fg2, fontSize: 12.5, height: 1.35)),
              ]))),
            ]),
          ),
        ),
        if (error != null) Padding(padding: const EdgeInsets.only(top: 8), child: Text(error!, style: TextStyle(color: t.crit, fontWeight: FontWeight.w600, fontSize: 13))),
        const SizedBox(height: 10),
        Row(children: [
          Expanded(child: OutlinedButton(onPressed: busy ? null : widget.onCancel, child: const Text('Cancel'))),
          const SizedBox(width: 10),
          Expanded(flex: 2, child: FilledButton(onPressed: busy ? null : _save, child: Text(busy ? 'Saving…' : 'Change password'))),
        ]),
      ]),
    );
  }
}
