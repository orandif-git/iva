import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../models.dart';
import '../repo.dart';
import '../scope.dart';
import '../theme.dart';
import '../widgets/brand.dart';
import '../widgets/camera_picture.dart';
import '../widgets/motion.dart';
import 'alert_rows.dart';
import 'common.dart';

/// Home answers one question: is everything OK? The answer first, then what is wrong, what needs you, today's numbers and the health of the system.
class HomePage extends StatefulWidget {
  const HomePage({super.key, required this.repo, required this.me, required this.onOpenAlerts, required this.onOpenCameras, this.refresh});
  final Listenable? refresh; // changes when the person comes back to this place
  final Repository repo;
  final Me? me;
  final VoidCallback onOpenAlerts, onOpenCameras;
  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  late Future<(Health, List<AlertItem>, Footfall)> data = _load();

  @override
  void initState() {
    super.initState();
    widget.refresh?.addListener(_reload);
  }

  @override
  void dispose() {
    widget.refresh?.removeListener(_reload);
    super.dispose();
  }

  Future<(Health, List<AlertItem>, Footfall)> _load() async {
    final r = await Future.wait([widget.repo.health(), widget.repo.alerts(), widget.repo.today()]);
    return (r[0] as Health, r[1] as List<AlertItem>, r[2] as Footfall);
  }

  void _reload() {
    if (mounted) setState(() { data = _load(); });
  }

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
        if (!snap.hasData) return const SkeletonPage(label: 'Loading your day');
        final (h, alerts, foot) = snap.data!;
        final wide = MediaQuery.sizeOf(context).width >= 900;
        final pad = wide ? 32.0 : 16.0;
        final groups = groupAlerts(alerts.where((a) => a.state == AlertState.open).toList());
        final recent = groups.where((g) => isRecent(g.latest)).toList();
        final earlier = groups.where((g) => !isRecent(g.latest)).toList();

        final payment = h.payment == null ? null : _Payment(h.payment!);
        final attention = h.issues.isEmpty ? null : _Attention(issues: h.issues, onCameras: widget.onOpenCameras, onAlerts: widget.onOpenAlerts);
        final needs = _Needs(recent: recent, earlier: earlier, repo: widget.repo, reload: _reload);
        final today = _Today(foot: foot, open: h.openAlerts);
        final health = _Health(h: h, onCameras: widget.onOpenCameras);

        return RefreshIndicator(
          onRefresh: () async => _reload(),
          child: ListView(padding: EdgeInsets.fromLTRB(pad, wide ? 0 : 16, pad, navClear), children: [
            if (wide) _Banner(h: h, onReview: widget.onOpenAlerts) else _Hero(h: h, me: widget.me, onReview: widget.onOpenAlerts),
            const SizedBox(height: 14),
            if (payment != null) ...[payment, const SizedBox(height: 14)],
            if (wide)
              IntrinsicHeight(
                child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                  Expanded(flex: 6, child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [if (attention != null) ...[attention, const SizedBox(height: 16)], Expanded(child: needs)])),
                  const SizedBox(width: 16),
                  Expanded(flex: 5, child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [today, const SizedBox(height: 16), Expanded(child: health)])),
                ]),
              )
            else ...[
              if (attention != null) ...[attention, const SizedBox(height: 14)],
              needs,
              const SizedBox(height: 14),
              today,
              _CameraStrip(repo: widget.repo, onOpen: widget.onOpenCameras),
              const SizedBox(height: 14),
              health,
            ],
            const SizedBox(height: 14),
            _SummaryCard(repo: widget.repo),
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

/// Phone: the greeting, then the answer in one pill. Green when all is well, amber when something needs a look.
class _Hero extends StatelessWidget {
  const _Hero({required this.h, required this.me, required this.onReview});
  final Health h;
  final Me? me;
  final VoidCallback onReview;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final ok = h.ok, n = h.issues.length;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Semantics(header: true, child: Text(me == null || me!.first.isEmpty ? _greeting() : '${_greeting()}, ${me!.first}', style: display(26))),
      const SizedBox(height: 2),
      Text(_today(), style: TextStyle(color: t.fg2, fontSize: 13)),
      const SizedBox(height: 10),
      StatusPill(ok ? 'Everything is working' : (n == 1 ? '1 thing needs attention' : '$n things need attention'), ok ? Level.ok : Level.warn),
    ]);
  }
}

/// A payment the customer owes, as one line: how much, for which invoice, and the day it is due. Amber before the day, red after it.
class _Payment extends StatelessWidget {
  const _Payment(this.p);
  final PaymentDue p;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final (bg, fg) = p.overdue ? (t.critSoft, t.crit) : (t.warnSoft, t.warnInk);
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(Radii.tile)),
      child: Row(children: [
        Icon(Icons.receipt_long_outlined, color: fg, size: 22),
        const SizedBox(width: 12),
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(p.overdue ? 'Payment was due on ${p.dueDate}' : 'Payment due on ${p.dueDate}', style: TextStyle(color: fg, fontWeight: FontWeight.w700, fontSize: 15)),
          Text([p.amount, if (p.invoice.isNotEmpty) 'Invoice ${p.invoice}'].join(' · '), style: TextStyle(color: fg, fontSize: 13)),
        ])),
      ]),
    );
  }
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
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(Radii.card)),
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
    if (total == 0) {
      return Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(color: t.surface, borderRadius: BorderRadius.circular(Radii.card), border: Border.all(color: t.line)),
        child: Row(children: [
          Container(width: 44, height: 44, decoration: BoxDecoration(color: t.okSoft, shape: BoxShape.circle), child: Icon(Icons.check_rounded, color: t.okInk, size: 24)),
          const SizedBox(width: 14),
          Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Text('Nothing needs you', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
            Text('We will message you on WhatsApp if something happens.', style: TextStyle(color: t.fg2, fontSize: 13.5)),
          ])),
        ]),
      );
    }
    return Section(
      icon: Icons.flag_outlined,
      title: 'Needs you',
      subtitle: 'Only things that are waiting for you.',
      trailing: total == 0 ? null : Pill('$total open', Level.bad),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        if (recent.isNotEmpty) _Featured(key: ValueKey('top${recent.first.key}${recent.first.latest.id}'), group: recent.first, repo: repo, reload: reload),
        if (recent.length > 1) ...[
          Divider(height: 25, thickness: 1, color: t.line),
          Group(bare: true, children: [for (final g in recent.skip(1)) AlertRow(key: ValueKey(g.key), group: g, repo: repo, onChanged: reload, alwaysOpen: true)]),
        ],
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
      decoration: BoxDecoration(color: t.surface2, borderRadius: BorderRadius.circular(Radii.button)),
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

/// Today in three figures, side by side.
class _Today extends StatelessWidget {
  const _Today({required this.foot, required this.open});
  final Footfall foot;
  final int open;
  @override
  Widget build(BuildContext context) => Semantics(label: 'Today', container: true, child: StatStrip([('People in', '${foot.peopleIn}'), ('Busiest hour', foot.peakHour ?? '–'), ('Open alerts', '$open')]));
}

/// The alert that most needs you, shown large: what the camera saw (when a picture was kept), what happened, and the two things you can do about it.
class _Featured extends StatefulWidget {
  const _Featured({super.key, required this.group, required this.repo, required this.reload});
  final AlertGroup group;
  final Repository repo;
  final VoidCallback reload;
  @override
  State<_Featured> createState() => _FeaturedState();
}

class _FeaturedState extends State<_Featured> {
  late final Future<Uint8List?> picture = widget.group.latest.hasEvidence ? widget.repo.alertImage(widget.group.latest.id).catchError((_) => null) : Future.value(null);
  bool busy = false;

  Future<void> _act(Future<void> Function() what) async {
    if (busy) return;
    setState(() => busy = true);
    try {
      await what();
    } finally {
      if (mounted) setState(() => busy = false);
    }
    widget.reload();
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final g = widget.group, a = g.latest;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      if (a.hasEvidence)
        Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(Radii.picture),
            child: SizedBox(
              height: 150,
              child: FutureBuilder<Uint8List?>(
                future: picture,
                builder: (context, p) => p.data != null
                    ? Image.memory(p.data!, fit: BoxFit.cover, gaplessPlayback: true, semanticLabel: 'What the camera saw: ${a.title}', errorBuilder: (_, __, ___) => Container(color: t.picture))
                    : (p.connectionState == ConnectionState.waiting ? const Skeleton(height: 150, radius: 0) : Container(color: t.picture)),
              ),
            ),
          ),
        ),
      Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Badge2(iconForType(a.rule, smart: a.smart), a.smart ? Level.ai : Level.bad, ai: a.smart, size: 40),
        const SizedBox(width: 12),
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(a.title, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
          const SizedBox(height: 2),
          Text(repeatsLine(g), style: TextStyle(color: t.fg2, fontSize: 13.5)),
        ])),
      ]),
      const SizedBox(height: 12),
      Row(children: [
        Expanded(child: FilledButton(style: FilledButton.styleFrom(minimumSize: const Size(44, 46), padding: const EdgeInsets.symmetric(horizontal: 8), shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(Radii.control))), onPressed: busy ? null : () => _act(() => doneGroup(widget.repo, g)), child: Text(g.openItems.length > 1 ? 'Done (${g.openItems.length})' : 'Done'))),
        const SizedBox(width: 10),
        Expanded(child: OutlinedButton(style: OutlinedButton.styleFrom(minimumSize: const Size(44, 46), padding: const EdgeInsets.symmetric(horizontal: 8), shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(Radii.control))), onPressed: busy ? null : () => _act(() => snoozeGroup(widget.repo, g)), child: const FittedBox(fit: BoxFit.scaleDown, child: Text('Snooze 1 hour')))),
      ]),
    ]);
  }
}

/// A row of your cameras to flick through, each with its latest picture. Tapping one opens Cameras.
class _CameraStrip extends StatefulWidget {
  const _CameraStrip({required this.repo, required this.onOpen});
  final Repository repo;
  final VoidCallback onOpen;
  @override
  State<_CameraStrip> createState() => _CameraStripState();
}

class _CameraStripState extends State<_CameraStrip> {
  late final Future<List<CameraItem>> cams = widget.repo.cameras().catchError((_) => <CameraItem>[]);

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return FutureBuilder<List<CameraItem>>(
      future: cams,
      builder: (context, snap) {
        final list = snap.data ?? const <CameraItem>[];
        if (list.isEmpty) return const SizedBox.shrink();
        return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          const SizedBox(height: 6),
          Row(children: [
            const Expanded(child: Text('Your cameras', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 16))),
            TextButton(onPressed: widget.onOpen, child: const Text('See all')),
          ]),
          SizedBox(
            height: 152,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              clipBehavior: Clip.none,
              itemCount: list.length > 6 ? 6 : list.length,
              separatorBuilder: (_, __) => const SizedBox(width: 12),
              itemBuilder: (context, i) {
                final c = list[i];
                return Pressable(
                  label: '${c.name}, ${c.working ? 'working' : 'offline'}',
                  onTap: widget.onOpen,
                  child: ExcludeSemantics(
                    child: SizedBox(
                      width: 150,
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        ClipRRect(
                          borderRadius: BorderRadius.circular(Radii.button),
                          child: SizedBox(
                            height: 96,
                            width: 150,
                            child: Stack(children: [
                              Positioned.fill(child: ColoredBox(color: c.working ? t.picture : t.surface2)),
                              Positioned.fill(child: CameraPicture(repo: widget.repo, cameraId: c.id, active: c.working, every: const Duration(seconds: 15))),
                              Positioned(left: 8, bottom: 8, child: Pill(c.working ? 'Live' : 'Offline', c.working ? Level.ok : Level.bad)),
                            ]),
                          ),
                        ),
                        const SizedBox(height: 8),
                        Text(c.name, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
                        Text(c.recorder.isEmpty ? (c.working ? 'Working' : 'Not sending pictures') : c.recorder, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: t.fg2, fontSize: 12.5)),
                      ]),
                    ),
                  ),
                );
              },
            ),
          ),
        ]);
      },
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
      final dot = l == Level.ok ? t.okInk : (l == Level.warn ? t.warnInk : t.crit);
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

/// The newest daily or weekly summary, the same text that goes to WhatsApp. Left out when there has not been one yet or it cannot be loaded.
class _SummaryCard extends StatelessWidget {
  const _SummaryCard({required this.repo});
  final Repository repo;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return FutureBuilder<SummaryItem?>(
      future: repo.summary(),
      builder: (context, snap) {
        final s = snap.data;
        if (s == null) return const SizedBox();
        final when = s.at == null ? '' : ' · ${ago(DateTime.now().difference(s.at!).inSeconds.clamp(0, 1 << 30))}';
        return Section(
          icon: Icons.summarize_outlined,
          title: s.kind == 'weekly' ? 'Weekly summary' : 'Daily summary',
          subtitle: 'The latest one$when.',
          child: Group(children: [
            for (final (heading, lines) in s.blocks)
              if (!(heading.isEmpty && lines.length == 1 && lines.first.startsWith('Open the ')))
                Padding(
                  padding: EdgeInsets.fromLTRB(cardPad(context), 12, cardPad(context), 12),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    if (heading.isNotEmpty) Text(heading, style: TextStyle(color: heading == 'NEEDS A LOOK' ? t.warnInk : t.fg2, fontSize: 12, fontWeight: FontWeight.w700, letterSpacing: .6)),
                    for (final l in lines) Padding(padding: EdgeInsets.only(top: heading.isEmpty ? 0 : 6), child: Text(l, style: TextStyle(fontSize: 14.5, fontWeight: heading.isEmpty ? FontWeight.w600 : FontWeight.w500))),
                  ]),
                ),
          ]),
        );
      },
    );
  }
}
