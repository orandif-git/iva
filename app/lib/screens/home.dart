import 'package:flutter/material.dart';

import '../models.dart';
import '../repo.dart';
import '../scope.dart';
import '../theme.dart';
import '../widgets/brand.dart';
import 'common.dart';

/// Home answers one question: is everything OK? A greeting and the answer first, then what needs you. No camera pictures and no repeated lists.
class HomePage extends StatefulWidget {
  const HomePage({super.key, required this.repo, required this.onOpenAlerts, required this.onOpenCameras});
  final Repository repo;
  final VoidCallback onOpenAlerts, onOpenCameras;
  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  late Future<(Health, List<AlertItem>, Footfall, Me)> data = _load();

  Future<(Health, List<AlertItem>, Footfall, Me)> _load() async {
    final r = await Future.wait([widget.repo.health(), widget.repo.alerts(), widget.repo.today(), widget.repo.me().catchError((_) => const Me('', '', ''))]);
    return (r[0] as Health, r[1] as List<AlertItem>, r[2] as Footfall, r[3] as Me);
  }

  void _reload() => setState(() { data = _load(); });

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return FutureBuilder(
      future: data,
      builder: (context, snap) {
        if (snap.hasError && snap.error is ApiError && (snap.error as ApiError).code == 'signed_out') {
          WidgetsBinding.instance.addPostFrameCallback((_) => AppScope.maybeOf(context)?.onSignedOut());
          return const SizedBox();
        }
        if (snap.hasError) {
          return EmptyState(icon: Icons.cloud_off_outlined, title: 'We could not load this', text: '${snap.error}', action: OutlinedButton(onPressed: _reload, child: const Text('Try again')));
        }
        if (!snap.hasData) return const Loading(text: 'Loading your day');
        final (h, alerts, foot, me) = snap.data!;
        final need = alerts.where((a) => a.state == AlertState.open).take(4).toList();
        final wide = MediaQuery.sizeOf(context).width >= 900;
        final pad = wide ? 32.0 : 16.0;
        return RefreshIndicator(
          onRefresh: () async => _reload(),
          child: ListView(padding: EdgeInsets.fromLTRB(pad, wide ? 4 : 16, pad, 40), children: [
            _Hero(h: h, me: me, onReview: widget.onOpenAlerts),
            const SizedBox(height: 16),
            if (wide)
              Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Expanded(flex: 6, child: _Need(need: need, repo: widget.repo, reload: _reload)),
                const SizedBox(width: 16),
                Expanded(flex: 5, child: Column(children: [_Numbers(foot: foot, alerts: alerts.length), const SizedBox(height: 16), _HealthPanel(h: h, onCameras: widget.onOpenCameras)])),
              ])
            else ...[
              _Need(need: need, repo: widget.repo, reload: _reload),
              const SizedBox(height: 16),
              _Numbers(foot: foot, alerts: alerts.length),
              const SizedBox(height: 16),
              _HealthPanel(h: h, onCameras: widget.onOpenCameras),
            ],
            const SizedBox(height: 22),
            Center(child: Text(tagline, style: TextStyle(color: t.fg2.withValues(alpha: .7), fontSize: 12, fontStyle: FontStyle.italic))),
          ]),
        );
      },
    );
  }
}

String _greeting() {
  final h = DateTime.now().hour;
  return h < 12 ? 'Good morning' : (h < 17 ? 'Good afternoon' : 'Good evening');
}

String _today() {
  const wd = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  const mo = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  final n = DateTime.now();
  return '${wd[n.weekday - 1]}, ${n.day} ${mo[n.month - 1]}';
}

class _Hero extends StatelessWidget {
  const _Hero({required this.h, required this.me, required this.onReview});
  final Health h;
  final Me me;
  final VoidCallback onReview;
  @override
  Widget build(BuildContext context) {
    final n = h.issues.length;
    final ok = h.ok;
    return ClipRRect(
      borderRadius: BorderRadius.circular(24),
      child: Container(
        decoration: BoxDecoration(gradient: ok ? brandGradient : const LinearGradient(colors: [Color(0xFF4A2A00), Color(0xFFB36A00)], begin: Alignment.topLeft, end: Alignment.bottomRight)),
        child: Stack(children: [
          Positioned.fill(child: CustomPaint(painter: FramePainter(color: Colors.white.withValues(alpha: .14), inset: 14, arm: 30, width: 3))),
          const Positioned(right: -60, bottom: -90, child: LensGlow(size: 280)),
          Padding(
            padding: const EdgeInsets.fromLTRB(28, 26, 28, 26),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${_today()}${me.business.isEmpty ? '' : ' · ${me.business}'}', style: TextStyle(color: Colors.white.withValues(alpha: .8), fontSize: 13.5, fontWeight: FontWeight.w500)),
              const SizedBox(height: 6),
              Text(me.first.isEmpty ? _greeting() : '${_greeting()}, ${me.first}', style: const TextStyle(color: Colors.white, fontSize: 28, fontWeight: FontWeight.w700, letterSpacing: -.8)),
              const SizedBox(height: 16),
              Wrap(spacing: 12, runSpacing: 12, crossAxisAlignment: WrapCrossAlignment.center, children: [
                Container(
                  padding: const EdgeInsets.fromLTRB(10, 8, 16, 8),
                  decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(99)),
                  child: Row(mainAxisSize: MainAxisSize.min, children: [
                    Container(width: 28, height: 28, decoration: BoxDecoration(color: ok ? const Color(0xFFDDF4E6) : const Color(0xFFFFF0D1), shape: BoxShape.circle), child: Icon(ok ? Icons.check_rounded : Icons.priority_high_rounded, size: 18, color: ok ? const Color(0xFF0B6B3A) : const Color(0xFF7A4700))),
                    const SizedBox(width: 10),
                    Flexible(child: Text(ok ? 'Everything is working' : (n == 1 ? '1 thing needs your attention' : '$n things need your attention'), style: const TextStyle(color: Color(0xFF0B1220), fontWeight: FontWeight.w700, fontSize: 15))),
                  ]),
                ),
                if (!ok) FilledButton(style: FilledButton.styleFrom(backgroundColor: Colors.white.withValues(alpha: .18), foregroundColor: Colors.white, minimumSize: const Size(44, 44)), onPressed: onReview, child: const Text('Review')),
              ]),
              if (!ok) Padding(padding: const EdgeInsets.only(top: 12), child: Text(h.issues.map((i) => i.title).join('  ·  '), style: TextStyle(color: Colors.white.withValues(alpha: .9), fontSize: 14))),
              if (ok) Padding(padding: const EdgeInsets.only(top: 12), child: Text('All cameras and recorders are connected. Alerts are on.', style: TextStyle(color: Colors.white.withValues(alpha: .85), fontSize: 14))),
            ]),
          ),
        ]),
      ),
    );
  }
}

class _Need extends StatelessWidget {
  const _Need({required this.need, required this.repo, required this.reload});
  final List<AlertItem> need;
  final Repository repo;
  final VoidCallback reload;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Section(
      icon: Icons.flag_outlined,
      title: 'Needs you',
      subtitle: 'Only things that are waiting for you.',
      trailing: need.isEmpty ? null : Pill('${need.length} open', Level.bad),
      child: need.isEmpty
          ? const EmptyState(icon: Icons.check_circle_outline_rounded, title: 'You are all caught up', text: 'We will message you on WhatsApp if something changes.')
          : Column(children: [
              for (var i = 0; i < need.length; i++) ...[
                if (i > 0) Divider(height: 22, color: t.line),
                _NeedRow(a: need[i], repo: repo, reload: reload),
              ],
            ]),
    );
  }
}

class _NeedRow extends StatelessWidget {
  const _NeedRow({required this.a, required this.repo, required this.reload});
  final AlertItem a;
  final Repository repo;
  final VoidCallback reload;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final narrow = MediaQuery.sizeOf(context).width < 560;
    final actions = Wrap(spacing: 6, children: [
      TextButton(onPressed: () async { await repo.snooze(a.id); reload(); }, child: const Text('Snooze')),
      FilledButton(style: FilledButton.styleFrom(minimumSize: const Size(44, 42)), onPressed: () async { await repo.done(a.id); reload(); }, child: const Text('Done')),
    ]);
    final info = Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Badge2(a.smart ? Icons.insights_rounded : Icons.warning_amber_rounded, a.smart ? Level.ai : Level.bad, ai: a.smart),
      const SizedBox(width: 14),
      Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(a.title, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5)),
        const SizedBox(height: 2),
        Text([a.where, a.when].where((x) => x.isNotEmpty).join(' · '), style: TextStyle(color: t.fg2, fontSize: 13)),
        if (a.smart) const Padding(padding: EdgeInsets.only(top: 6), child: SmartTag()),
      ])),
    ]);
    return narrow ? Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [info, const SizedBox(height: 8), Align(alignment: Alignment.centerRight, child: actions)]) : Row(children: [Expanded(child: info), const SizedBox(width: 12), actions]);
  }
}

class _Numbers extends StatelessWidget {
  const _Numbers({required this.foot, required this.alerts});
  final Footfall foot;
  final int alerts;
  @override
  Widget build(BuildContext context) => IntrinsicHeight(child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Expanded(child: _Stat(Icons.groups_rounded, 'People in today', '${foot.peopleIn}', foot.peakHour == null ? null : 'Busiest: ${foot.peakHour}', Level.ok)),
        const SizedBox(width: 12),
        Expanded(child: _Stat(Icons.storefront_rounded, 'Inside now', '${foot.insideNow}', null, Level.mute)),
        const SizedBox(width: 12),
        Expanded(child: _Stat(Icons.notifications_rounded, 'Alerts', '$alerts', null, alerts == 0 ? Level.ok : Level.warn)),
      ]));
}

class _Stat extends StatelessWidget {
  const _Stat(this.icon, this.label, this.value, this.note, this.level);
  final IconData icon;
  final String label, value;
  final String? note;
  final Level level;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final (bg, fg) = levelColors(t, level == Level.mute ? Level.ok : level);
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(color: t.surface, borderRadius: BorderRadius.circular(20), border: Border.all(color: t.line), boxShadow: t.lift),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Container(width: 34, height: 34, decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(11)), child: Icon(icon, size: 19, color: fg)),
        const SizedBox(height: 12),
        FittedBox(fit: BoxFit.scaleDown, alignment: Alignment.centerLeft, child: Text(value, style: const TextStyle(fontSize: 30, fontWeight: FontWeight.w700, height: 1.1, letterSpacing: -1))),
        const SizedBox(height: 2),
        Text(label, style: TextStyle(color: t.fg2, fontSize: 12.5, fontWeight: FontWeight.w500), maxLines: 2),
        if (note != null) Text(note!, style: TextStyle(color: t.brand, fontSize: 12, fontWeight: FontWeight.w600)),
      ]),
    );
  }
}

class _HealthPanel extends StatelessWidget {
  const _HealthPanel({required this.h, required this.onCameras});
  final Health h;
  final VoidCallback onCameras;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    Widget row(IconData icon, String k, String v, Level l, {VoidCallback? onTap}) {
      final (bg, fg) = levelColors(t, l);
      return InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 4),
          child: Row(children: [
            Container(width: 38, height: 38, decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(12)), child: Icon(icon, size: 20, color: fg)),
            const SizedBox(width: 14),
            Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(k, style: TextStyle(color: t.fg2, fontSize: 12.5)), Text(v, style: const TextStyle(fontWeight: FontWeight.w700))])),
            Container(width: 10, height: 10, decoration: BoxDecoration(color: l == Level.ok ? t.okInk : (l == Level.warn ? const Color(0xFFD98A00) : t.crit), shape: BoxShape.circle)),
            if (onTap != null) Padding(padding: const EdgeInsets.only(left: 6), child: Icon(Icons.chevron_right_rounded, color: t.fg2, size: 20)),
          ]),
        ),
      );
    }

    return Section(
      icon: Icons.monitor_heart_outlined,
      title: 'System health',
      subtitle: 'One look to know the cameras and the connection are fine.',
      child: Column(children: [
        row(Icons.dns_outlined, 'Recorders', '${h.recordersOnline} of ${h.recordersTotal} online', h.recordersOnline == h.recordersTotal ? Level.ok : Level.bad),
        row(Icons.videocam_outlined, 'Cameras', '${h.camerasWorking} of ${h.camerasTotal} working', h.camerasWorking == h.camerasTotal ? Level.ok : Level.bad, onTap: onCameras),
        row(Icons.chat_outlined, 'WhatsApp alerts', h.failed24h == 0 ? 'Working' : '${h.failed24h} failed in 24 h', h.failed24h == 0 ? Level.ok : Level.warn),
        row(Icons.update_rounded, 'Latest data', ago(h.dataSecondsAgo), (h.dataSecondsAgo ?? 999) < 120 ? Level.ok : Level.warn),
      ]),
    );
  }
}
