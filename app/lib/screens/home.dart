import 'package:flutter/material.dart';

import '../models.dart';
import '../repo.dart';
import '../scope.dart';
import '../theme.dart';
import '../widgets/brand.dart';
import 'alert_rows.dart';
import 'common.dart';

/// Home answers one question: is everything OK? The answer first, then what is wrong, what needs you, today's numbers and the health of the system.
class HomePage extends StatefulWidget {
  const HomePage({super.key, required this.repo, required this.me, required this.onOpenAlerts, required this.onOpenCameras});
  final Repository repo;
  final Me? me;
  final VoidCallback onOpenAlerts, onOpenCameras;
  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  late Future<(Health, List<AlertItem>, Footfall)> data = _load();

  Future<(Health, List<AlertItem>, Footfall)> _load() async {
    final r = await Future.wait([widget.repo.health(), widget.repo.alerts(), widget.repo.today()]);
    return (r[0] as Health, r[1] as List<AlertItem>, r[2] as Footfall);
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
        final (h, alerts, foot) = snap.data!;
        final wide = MediaQuery.sizeOf(context).width >= 900;
        final pad = wide ? 32.0 : 16.0;
        final groups = groupAlerts(alerts.where((a) => a.state == AlertState.open).toList());
        final recent = groups.where((g) => isRecent(g.latest)).toList();
        final earlier = groups.where((g) => !isRecent(g.latest)).toList();

        final attention = h.issues.isEmpty ? null : _Attention(issues: h.issues, onCameras: widget.onOpenCameras, onAlerts: widget.onOpenAlerts);
        final needs = _Needs(recent: recent, earlier: earlier, repo: widget.repo, reload: _reload);
        final today = _Today(foot: foot, open: h.openAlerts);
        final health = _Health(h: h, onCameras: widget.onOpenCameras);

        return RefreshIndicator(
          onRefresh: () async => _reload(),
          child: ListView(padding: EdgeInsets.fromLTRB(pad, wide ? 0 : 16, pad, navClear), children: [
            if (wide) _Banner(h: h, onReview: widget.onOpenAlerts) else _Hero(h: h, me: widget.me, onReview: widget.onOpenAlerts),
            const SizedBox(height: 16),
            if (wide)
              IntrinsicHeight(
                child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                  Expanded(flex: 6, child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [if (attention != null) ...[attention, const SizedBox(height: 16)], Expanded(child: needs)])),
                  const SizedBox(width: 16),
                  Expanded(flex: 5, child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [today, const SizedBox(height: 16), Expanded(child: health)])),
                ]),
              )
            else ...[
              if (attention != null) ...[attention, const SizedBox(height: 16)],
              needs,
              const SizedBox(height: 16),
              today,
              const SizedBox(height: 16),
              health,
            ],
            const SizedBox(height: 24),
            if (!wide) Center(child: Text(tagline, style: TextStyle(color: t.fg2.withValues(alpha: .7), fontSize: 12, fontStyle: FontStyle.italic))),
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

/// Phone: the greeting and the answer. Always the brand colour; a problem shows as an amber chip, not a muddy hero.
class _Hero extends StatelessWidget {
  const _Hero({required this.h, required this.me, required this.onReview});
  final Health h;
  final Me? me;
  final VoidCallback onReview;
  @override
  Widget build(BuildContext context) {
    final ok = h.ok, n = h.issues.length;
    return ClipRRect(
      borderRadius: BorderRadius.circular(22),
      child: Container(
        decoration: const BoxDecoration(gradient: brandGradient),
        child: Stack(children: [
          Positioned(right: -34, bottom: -50, child: IgnorePointer(child: CustomPaint(size: const Size(170, 170), painter: _RingsPainter()))),
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 20, 20, 20),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${_today()}${me == null || me!.business.isEmpty ? '' : ' · ${me!.business}'}', style: TextStyle(color: Colors.white.withValues(alpha: .82), fontSize: 13, fontWeight: FontWeight.w500)),
              const SizedBox(height: 4),
              Text(me == null || me!.first.isEmpty ? _greeting() : '${_greeting()}, ${me!.first}', style: const TextStyle(color: Colors.white, fontSize: 26, fontWeight: FontWeight.w700, letterSpacing: -.7, height: 1.15)),
              const SizedBox(height: 16),
              _StatusChip(ok: ok, text: ok ? 'Everything is working' : (n == 1 ? '1 thing needs attention' : '$n things need attention')),
              if (ok) Padding(padding: const EdgeInsets.only(top: 10), child: Text('All cameras and recorders are connected.', style: TextStyle(color: Colors.white.withValues(alpha: .85), fontSize: 13.5))),
            ]),
          ),
        ]),
      ),
    );
  }
}

class _StatusChip extends StatelessWidget {
  const _StatusChip({required this.ok, required this.text});
  final bool ok;
  final String text;
  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.fromLTRB(8, 7, 14, 7),
        decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(99)),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          Container(width: 24, height: 24, decoration: BoxDecoration(color: ok ? const Color(0xFFDDF4E6) : const Color(0xFFFFE9B8), shape: BoxShape.circle), child: Icon(ok ? Icons.check_rounded : Icons.priority_high_rounded, size: 16, color: ok ? const Color(0xFF0B6B3A) : const Color(0xFF7A4700))),
          const SizedBox(width: 8),
          Flexible(child: Text(text, style: const TextStyle(color: Color(0xFF0B1220), fontWeight: FontWeight.w700, fontSize: 14.5))),
        ]),
      );
}

class _RingsPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size s) {
    final c = Offset(s.width / 2, s.height / 2);
    for (var i = 1; i <= 4; i++) {
      canvas.drawCircle(c, s.width / 2 * i / 4, Paint()..color = Colors.white.withValues(alpha: .10 - i * .015)..style = PaintingStyle.stroke..strokeWidth = 2);
    }
  }

  @override
  bool shouldRepaint(_RingsPainter o) => false;
}

/// Wide screens: the greeting is in the page header, so the answer is a slim banner.
class _Banner extends StatelessWidget {
  const _Banner({required this.h, required this.onReview});
  final Health h;
  final VoidCallback onReview;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final ok = h.ok, n = h.issues.length;
    final (bg, fg) = ok ? (t.okSoft, t.okInk) : (t.warnSoft, t.warnInk);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(18)),
      child: Row(children: [
        Icon(ok ? Icons.check_circle_rounded : Icons.error_rounded, color: fg, size: 26),
        const SizedBox(width: 12),
        Expanded(child: Text(ok ? 'Everything is working. All cameras and recorders are connected.' : (n == 1 ? '1 thing needs your attention' : '$n things need your attention'), style: TextStyle(color: fg, fontWeight: FontWeight.w700, fontSize: 15.5))),
        if (!ok) FilledButton.tonal(onPressed: onReview, child: const Text('Review')),
      ]),
    );
  }
}

class _Attention extends StatelessWidget {
  const _Attention({required this.issues, required this.onCameras, required this.onAlerts});
  final List<Issue> issues;
  final VoidCallback onCameras, onAlerts;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    IconData icon(String k) => switch (k) { 'camera_down' => Icons.videocam_off_outlined, 'recorder_offline' => Icons.wifi_off_rounded, 'recorder_unstable' => Icons.sync_problem_outlined, 'messages_failing' || 'messages_stuck' => Icons.chat_outlined, _ => Icons.update_rounded };
    return Section(
      icon: Icons.error_outline_rounded,
      title: 'Needs attention',
      subtitle: 'Something about your equipment or connection.',
      child: Group(bare: true, children: [
        for (final i in issues.take(4))
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 12),
            child: Row(children: [
              Badge2(icon(i.kind), i.level == 'critical' ? Level.bad : Level.warn, size: 40),
              const SizedBox(width: 12),
              Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(i.title, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14.5)), Text(i.level == 'critical' ? 'Fix now' : 'Look soon', style: TextStyle(color: i.level == 'critical' ? t.crit : t.warnInk, fontSize: 12.5, fontWeight: FontWeight.w600))])),
              const SizedBox(width: 8),
              OutlinedButton(style: OutlinedButton.styleFrom(minimumSize: const Size(44, 40), padding: const EdgeInsets.symmetric(horizontal: 14)), onPressed: i.kind == 'camera_down' ? onCameras : onAlerts, child: const Text('Check')),
            ]),
          ),
        if (issues.length > 4)
          Padding(padding: const EdgeInsets.symmetric(vertical: 8), child: Align(alignment: Alignment.centerLeft, child: TextButton(onPressed: onAlerts, child: Text('and ${issues.length - 4} more')))),
      ]),
    );
  }
}

class _Needs extends StatelessWidget {
  const _Needs({required this.recent, required this.earlier, required this.repo, required this.reload});
  final List<AlertGroup> recent, earlier;
  final Repository repo;
  final VoidCallback reload;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final total = recent.length + earlier.length;
    return Section(
      icon: Icons.flag_outlined,
      title: 'Needs you',
      subtitle: 'Only things that are waiting for you.',
      trailing: total == 0 ? null : Pill('$total open', Level.bad),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        if (total == 0) const EmptyState(icon: Icons.check_circle_outline_rounded, title: 'You are all caught up', text: 'We will message you on WhatsApp if something changes.'),
        if (recent.isNotEmpty) Group(bare: true, children: [for (final g in recent) AlertRow(key: ValueKey(g.key), group: g, repo: repo, onChanged: reload, alwaysOpen: true)]),
        if (earlier.isNotEmpty) ...[
          const SizedBox(height: 12),
          _Earlier(groups: earlier, repo: repo, reload: reload),
        ],
        if (recent.isEmpty && earlier.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 8), child: Text('Nothing new in the last 24 hours.', style: TextStyle(color: t.fg2, fontSize: 13))),
      ]),
    );
  }
}

/// Open alerts older than a day, folded away so they do not crowd the page.
class _Earlier extends StatelessWidget {
  const _Earlier({required this.groups, required this.repo, required this.reload});
  final List<AlertGroup> groups;
  final Repository repo;
  final VoidCallback reload;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final n = groups.fold<int>(0, (s, g) => s + g.openItems.length);
    return Container(
      decoration: BoxDecoration(color: t.surface2, borderRadius: BorderRadius.circular(16)),
      child: Theme(
        data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
        child: ExpansionTile(
          tilePadding: const EdgeInsets.symmetric(horizontal: 16),
          shape: const Border(),
          collapsedShape: const Border(),
          title: Text('Earlier · $n open', style: const TextStyle(fontWeight: FontWeight.w700)),
          subtitle: Text('Older than a day', style: TextStyle(color: t.fg2, fontSize: 12.5)),
          childrenPadding: EdgeInsets.zero,
          children: [
            Group(children: [for (final g in groups) AlertRow(key: ValueKey('e${g.key}'), group: g, repo: repo, onChanged: reload)]),
            Padding(
              padding: const EdgeInsets.all(12),
              child: Align(alignment: Alignment.centerRight, child: TextButton(onPressed: () async { for (final g in groups) { await doneGroup(repo, g); } reload(); }, child: const Text('Mark all earlier as done'))),
            ),
          ],
        ),
      ),
    );
  }
}

/// Three aligned figures in one card, so the numbers always line up.
class _Today extends StatelessWidget {
  const _Today({required this.foot, required this.open});
  final Footfall foot;
  final int open;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    Widget cell(IconData icon, String value, String label, Color tint) => Expanded(
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Column(children: [
              Icon(icon, size: 20, color: tint),
              const SizedBox(height: 8),
              FittedBox(fit: BoxFit.scaleDown, child: Text(value, style: const TextStyle(fontSize: 28, fontWeight: FontWeight.w700, letterSpacing: -.8, height: 1.1, fontFeatures: [FontFeature.tabularFigures()]))),
              const SizedBox(height: 4),
              Text(label, textAlign: TextAlign.center, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: t.fg2, fontSize: 12.5, fontWeight: FontWeight.w500)),
            ]),
          ),
        );
    return Section(
      icon: Icons.today_outlined,
      title: 'Today',
      child: IntrinsicHeight(
        child: Row(children: [
          cell(Icons.groups_rounded, '${foot.peopleIn}', 'People in', t.okInk),
          VerticalDivider(width: 1, color: t.line),
          cell(Icons.schedule_rounded, foot.peakHour ?? '–', 'Busiest hour', t.accent),
          VerticalDivider(width: 1, color: t.line),
          cell(Icons.notifications_rounded, '$open', 'Open alerts', open == 0 ? t.okInk : t.warnInk),
        ]),
      ),
    );
  }
}

class _Health extends StatelessWidget {
  const _Health({required this.h, required this.onCameras});
  final Health h;
  final VoidCallback onCameras;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    Widget row(IconData icon, String k, String v, Level l, {VoidCallback? onTap}) {
      final dot = l == Level.ok ? t.okInk : (l == Level.warn ? const Color(0xFFD98A00) : t.crit);
      return InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 12),
          child: Row(children: [
            Icon(icon, size: 22, color: t.fg2),
            const SizedBox(width: 14),
            Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(k, style: TextStyle(color: t.fg2, fontSize: 12.5)), Text(v, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15))])),
            Container(width: 10, height: 10, decoration: BoxDecoration(color: dot, shape: BoxShape.circle)),
            SizedBox(width: 24, child: onTap == null ? null : Icon(Icons.chevron_right_rounded, color: t.fg2, size: 20)),
          ]),
        ),
      );
    }

    return Section(
      icon: Icons.monitor_heart_outlined,
      title: 'System health',
      subtitle: 'Cameras, recorders and alert delivery.',
      child: Group(bare: true, children: [
        row(Icons.dns_outlined, 'Recorders', '${h.recordersOnline} of ${h.recordersTotal} online', h.recordersOnline == h.recordersTotal ? Level.ok : Level.bad),
        row(Icons.videocam_outlined, 'Cameras', '${h.camerasWorking} of ${h.camerasTotal} working', h.camerasWorking == h.camerasTotal ? Level.ok : Level.bad, onTap: onCameras),
        row(Icons.chat_outlined, 'WhatsApp alerts', h.failed24h == 0 ? 'Working' : '${h.failed24h} failed in 24 h', h.failed24h == 0 ? Level.ok : Level.warn),
        row(Icons.update_rounded, 'Latest data', ago(h.dataSecondsAgo), (h.dataSecondsAgo ?? 999) < 120 ? Level.ok : Level.warn),
      ]),
    );
  }
}
