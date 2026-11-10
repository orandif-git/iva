import 'package:flutter/material.dart';

import '../models.dart';
import '../repo.dart';
import '../scope.dart';
import '../theme.dart';
import 'common.dart';

/// Home answers one question: is everything OK? Health first, then what needs you. No camera pictures and no repeated lists.
class HomePage extends StatefulWidget {
  const HomePage({super.key, required this.repo, required this.onOpenAlerts, required this.onOpenCameras});
  final Repository repo;
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
  void _reload() => setState(() => data = _load());

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
        if (snap.hasError) return Center(child: Column(mainAxisSize: MainAxisSize.min, children: [Text('${snap.error}'), const SizedBox(height: 8), OutlinedButton(onPressed: _reload, child: const Text('Try again'))]));
        if (!snap.hasData) return const Center(child: CircularProgressIndicator());
        final (h, alerts, foot) = snap.data!;
        final need = alerts.where((a) => a.state == AlertState.open).take(3).toList();
        return RefreshIndicator(
          onRefresh: () async => _reload(),
          child: ListView(padding: const EdgeInsets.all(16), children: [
            _Banner(h, widget.onOpenAlerts),
            const SizedBox(height: 14),
            Section(
              title: 'Needs you',
              subtitle: 'Only things that are waiting for you.',
              child: need.isEmpty
                  ? const Padding(padding: EdgeInsets.symmetric(vertical: 8), child: Text('You are all caught up. We will message you on WhatsApp if something changes.'))
                  : Column(children: [
                      for (final a in need)
                        ListTile(
                          contentPadding: EdgeInsets.zero,
                          leading: Badge2(a.smart ? Icons.auto_awesome : Icons.warning_amber_rounded, a.smart ? Level.ai : Level.bad),
                          title: Text(a.title, style: const TextStyle(fontWeight: FontWeight.w600)),
                          subtitle: Text([a.where, a.when].where((x) => x.isNotEmpty).join(' · ')),
                          trailing: Wrap(spacing: 6, children: [
                            TextButton(onPressed: () async { await widget.repo.snooze(a.id); _reload(); }, child: const Text('Snooze')),
                            FilledButton(onPressed: () async { await widget.repo.done(a.id); _reload(); }, child: const Text('Done')),
                          ]),
                        ),
                    ]),
            ),
            const SizedBox(height: 14),
            IntrinsicHeight(
              child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                Expanded(child: _Num('People in today', '${foot.peopleIn}', t)),
                const SizedBox(width: 10),
                Expanded(child: _Num('Inside now', '${foot.insideNow}', t, note: foot.peakHour == null ? null : 'Busiest hour: ${foot.peakHour}')),
                const SizedBox(width: 10),
                Expanded(child: _Num('Alerts', '${alerts.length}', t)),
              ]),
            ),
            const SizedBox(height: 14),
            const SizedBox(height: 14),
            Section(
              title: 'System health',
              subtitle: 'One look to know the cameras and the connection are fine.',
              child: Wrap(spacing: 10, runSpacing: 10, children: [
                _Health('Recorders', '${h.recordersOnline} of ${h.recordersTotal} online', h.recordersOnline == h.recordersTotal ? Level.ok : Level.bad, t),
                _Health('Cameras', '${h.camerasWorking} of ${h.camerasTotal} working', h.camerasWorking == h.camerasTotal ? Level.ok : Level.bad, t, onTap: widget.onOpenCameras),
                _Health('WhatsApp alerts', h.failed24h == 0 ? 'Working' : '${h.failed24h} failed', h.failed24h == 0 ? Level.ok : Level.warn, t),
                _Health('Latest data', ago(h.dataSecondsAgo), (h.dataSecondsAgo ?? 999) < 120 ? Level.ok : Level.warn, t),
              ]),
            ),
          ]),
        );
      },
    );
  }
}

class _Banner extends StatelessWidget {
  const _Banner(this.h, this.onReview);
  final Health h;
  final VoidCallback onReview;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final n = h.issues.length;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Row(children: [
          Badge2(h.ok ? Icons.check_rounded : Icons.warning_amber_rounded, h.ok ? Level.ok : Level.warn, size: 52),
          const SizedBox(width: 14),
          Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(h.ok ? 'Everything is working' : (n == 1 ? '1 thing needs your attention' : '$n things need your attention'), style: const TextStyle(fontSize: 19, fontWeight: FontWeight.w700)),
            Text(h.ok ? 'All cameras and recorders are connected. Alerts are on.' : h.issues.map((i) => i.title).join(' · '), style: TextStyle(color: t.fg2)),
          ])),
          if (!h.ok) FilledButton(onPressed: onReview, child: const Text('Review')),
        ]),
      ),
    );
  }
}

class _Num extends StatelessWidget {
  const _Num(this.label, this.value, this.t, {this.note});
  final String label, value;
  final String? note;
  final Tok t;
  @override
  Widget build(BuildContext context) => Card(child: Padding(padding: const EdgeInsets.all(12), child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisAlignment: MainAxisAlignment.center, children: [
        Text(label, style: TextStyle(color: t.fg2, fontSize: 12.5), maxLines: 2),
        Text(value, style: const TextStyle(fontSize: 28, fontWeight: FontWeight.w700, height: 1.15)),
        if (note != null) Text(note!, style: TextStyle(color: t.fg2, fontSize: 12)),
      ])));
}

class _Health extends StatelessWidget {
  const _Health(this.k, this.v, this.level, this.t, {this.onTap});
  final String k, v;
  final Level level;
  final Tok t;
  final VoidCallback? onTap;
  @override
  Widget build(BuildContext context) {
    final dot = switch (level) { Level.ok => t.okInk, Level.warn => const Color(0xFFD98A00), _ => t.crit };
    return SizedBox(
      width: 210,
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.all(12),
          constraints: const BoxConstraints(minHeight: 64),
          decoration: BoxDecoration(color: t.surface2, borderRadius: BorderRadius.circular(14), border: Border.all(color: t.line)),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(k, style: TextStyle(color: t.fg2, fontSize: 12.5)),
            Row(children: [Container(width: 10, height: 10, decoration: BoxDecoration(color: dot, shape: BoxShape.circle)), const SizedBox(width: 8), Flexible(child: Text(v, style: const TextStyle(fontWeight: FontWeight.w700)))]),
          ]),
        ),
      ),
    );
  }
}
