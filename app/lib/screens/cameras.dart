import 'package:flutter/material.dart';

import '../live/live_view.dart';
import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import '../widgets/brand.dart';
import '../widgets/camera_picture.dart';
import 'camera_setup.dart';
import 'common.dart';

/// Every camera, grouped by the recorder it belongs to, with a one-line answer to "are they all working?" at the top.
class CamerasPage extends StatefulWidget {
  const CamerasPage({super.key, required this.repo, required this.onAddAlert});
  final Repository repo;
  final void Function(CameraItem) onAddAlert;
  @override
  State<CamerasPage> createState() => _CamerasPageState();
}

class _CamerasPageState extends State<CamerasPage> {
  late Future<List<CameraItem>> data = widget.repo.cameras();
  bool onlyProblems = false;
  String query = '';

  void _reload() => setState(() { data = widget.repo.cameras(); });

  Future<void> _open(CameraItem c) async {
    await Navigator.of(context).push<bool>(MaterialPageRoute(builder: (_) => CameraDetail(repo: widget.repo, camera: c, onAddAlert: widget.onAddAlert)));
    if (mounted) _reload();         // the camera may have been renamed or removed
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final wide = MediaQuery.sizeOf(context).width >= 900;
    final pad = wide ? 32.0 : 16.0;
    return FutureBuilder(
      future: data,
      builder: (context, snap) {
        if (snap.hasError) return EmptyState(icon: Icons.cloud_off_outlined, title: 'We could not load your cameras', text: '${snap.error}', action: OutlinedButton(onPressed: _reload, child: const Text('Try again')));
        if (!snap.hasData) return const Loading(text: 'Loading your cameras');
        final cams = snap.data!;
        if (cams.isEmpty) return const EmptyState(icon: Icons.videocam_off_outlined, title: 'No cameras yet', text: 'When your recorder is connected, its cameras appear here. Recorders are set up in the current app for now.');
        final bad = cams.where((c) => !c.working).length;
        final shown = cams.where((c) => (!onlyProblems || !c.working) && (query.isEmpty || c.name.toLowerCase().contains(query.toLowerCase()))).toList();
        final byRecorder = <String, List<CameraItem>>{};
        for (final c in shown) {
          (byRecorder[c.recorder.isEmpty ? 'Cameras' : c.recorder] ??= []).add(c);
        }
        return RefreshIndicator(
          onRefresh: () async => _reload(),
          child: ListView(padding: EdgeInsets.fromLTRB(pad, wide ? 0 : 12, pad, navClear), children: [
            _Summary(total: cams.length, bad: bad),
            if (bad > 0 || cams.length > 8) ...[
              const SizedBox(height: 14),
              Row(children: [
                if (bad > 0) Expanded(child: SegmentedButton<bool>(showSelectedIcon: false, segments: [const ButtonSegment(value: false, label: Text('All')), ButtonSegment(value: true, label: Text('Needs attention ($bad)', maxLines: 1, overflow: TextOverflow.ellipsis))], selected: {onlyProblems}, onSelectionChanged: (v) => setState(() => onlyProblems = v.first))),
                if (bad > 0 && cams.length > 8) const SizedBox(width: 12),
                if (cams.length > 8) Expanded(child: TextField(onChanged: (v) => setState(() => query = v), decoration: const InputDecoration(hintText: 'Find a camera', prefixIcon: Icon(Icons.search_rounded), isDense: true))),
              ]),
            ],
            if (shown.isEmpty) const EmptyState(icon: Icons.search_off_rounded, title: 'No cameras match', text: 'Try another name, or show all cameras.'),
            for (final e in byRecorder.entries) ...[
              GroupLabel(e.key, trailing: Text('${e.value.length} ${e.value.length == 1 ? 'camera' : 'cameras'}${e.value.any((c) => !c.working) ? ' · ${e.value.where((c) => !c.working).length} offline' : ''}', style: TextStyle(color: t.fg2, fontSize: 12.5, fontWeight: FontWeight.w600))),
              LayoutBuilder(builder: (context, box) {
                final cols = box.maxWidth > 900 ? 4 : box.maxWidth > 600 ? 3 : 2;
                return GridView.count(shrinkWrap: true, physics: const NeverScrollableScrollPhysics(), crossAxisCount: cols, mainAxisSpacing: 12, crossAxisSpacing: 12, childAspectRatio: 1.12, children: [for (final c in e.value) _CamCard(widget.repo, c, () => _open(c))]);
              }),
            ],
          ]),
        );
      },
    );
  }
}

class _Summary extends StatelessWidget {
  const _Summary({required this.total, required this.bad});
  final int total, bad;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final ok = bad == 0;
    final (bg, fg) = ok ? (t.okSoft, t.okInk) : (t.warnSoft, t.warnInk);
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(18)),
      child: Row(children: [
        Icon(ok ? Icons.check_circle_rounded : Icons.error_rounded, color: fg, size: 28),
        const SizedBox(width: 12),
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(ok ? (total == 1 ? 'Your camera is working' : 'All $total cameras are working') : '$bad of $total cameras need attention', style: TextStyle(color: fg, fontWeight: FontWeight.w700, fontSize: 16)),
          Text('Tap a camera to watch live', style: TextStyle(color: fg.withValues(alpha: .85), fontSize: 13)),
        ])),
        Icon(Icons.play_circle_outline_rounded, color: fg.withValues(alpha: .7)),
      ]),
    );
  }
}

class _CamCard extends StatefulWidget {
  const _CamCard(this.repo, this.c, this.onTap);
  final Repository repo;
  final CameraItem c;
  final VoidCallback onTap;
  @override
  State<_CamCard> createState() => _CamCardState();
}

class _CamCardState extends State<_CamCard> {
  bool hasPicture = false;

  @override
  Widget build(BuildContext context) {
    final c = widget.c, onTap = widget.onTap;
    final t = Tok.of(context);
    return Semantics(
      button: true,
      label: '${c.name}, ${c.working ? 'working' : 'offline'}',
      child: InkWell(
        borderRadius: BorderRadius.circular(20),
        onTap: onTap,
        child: Container(
          decoration: BoxDecoration(color: t.surface, borderRadius: BorderRadius.circular(20), border: Border.all(color: t.line), boxShadow: t.lift),
          clipBehavior: Clip.antiAlias,
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Expanded(
              child: Stack(children: [
                Positioned.fill(child: DecoratedBox(decoration: BoxDecoration(gradient: c.working ? const LinearGradient(colors: [Color(0xFF16304F), Color(0xFF2E5A8A)], begin: Alignment.topLeft, end: Alignment.bottomRight) : LinearGradient(colors: [t.surface2, t.line])))),
                // the camera's latest picture, fetched again every 5 seconds while the tile is on the screen
                Positioned.fill(child: CameraPicture(repo: widget.repo, cameraId: c.id, active: c.working, onPicture: (_) => setState(() => hasPicture = true))),
                Positioned.fill(child: CustomPaint(painter: FramePainter(color: (c.working ? Colors.white : t.fg2).withValues(alpha: hasPicture ? .5 : .22), inset: 10, arm: 16, width: 2))),
                if (!hasPicture) Center(child: Container(width: 44, height: 44, decoration: BoxDecoration(color: (c.working ? Colors.white : t.fg2).withValues(alpha: .18), shape: BoxShape.circle), child: Icon(c.working ? Icons.play_arrow_rounded : Icons.videocam_off_rounded, color: (c.working ? Colors.white : t.fg2).withValues(alpha: .9), size: 26))),
                Positioned(left: 10, top: 10, child: Pill(c.working ? 'Working' : 'Offline', c.working ? Level.ok : Level.bad, dot: true)),
              ]),
            ),
            Padding(padding: const EdgeInsets.fromLTRB(14, 10, 14, 12), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(c.name, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15), maxLines: 1, overflow: TextOverflow.ellipsis),
              Text(c.working ? (c.seenAgo == null ? 'Live view available' : 'Seen ${shortAgo(c.seenAgo)}') : 'Not sending pictures', style: TextStyle(color: c.working ? t.fg2 : t.crit, fontSize: 12.5), maxLines: 1, overflow: TextOverflow.ellipsis),
            ])),
          ]),
        ),
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow(this.icon, this.label, this.value, {this.tint, this.small = false});
  final IconData icon;
  final String label, value;
  final Color? tint;
  final bool small;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 13),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SizedBox(width: 22, child: Icon(icon, size: small ? 11 : 20, color: tint ?? t.fg2)),
        const SizedBox(width: 14),
        SizedBox(width: 128, child: Text(label, style: TextStyle(color: t.fg2, fontSize: 14))),
        Expanded(child: Text(value, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14.5, height: 1.35))),
      ]),
    );
  }
}

/// A camera, full screen on a phone. Live view and AI view start here and nowhere else.
class CameraDetail extends StatefulWidget {
  const CameraDetail({super.key, required this.repo, required this.camera, required this.onAddAlert});
  final Repository repo;
  final CameraItem camera;
  final void Function(CameraItem) onAddAlert;
  @override
  State<CameraDetail> createState() => _CameraDetailState();
}

class _CameraDetailState extends State<CameraDetail> {
  late String name = widget.camera.name;

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final repo = widget.repo, camera = widget.camera, onAddAlert = widget.onAddAlert;
    final narrow = MediaQuery.sizeOf(context).width < 700;
    return Scaffold(
      appBar: AppBar(title: Text(name), bottom: PreferredSize(preferredSize: const Size.fromHeight(1), child: Divider(height: 1, color: t.line))),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 900),
          child: SelectionArea(
            child: ListView(padding: const EdgeInsets.all(16), children: [
              LiveView(repo: repo, camera: camera, compact: narrow),
              const SizedBox(height: 16),
              const GroupLabel('About this camera'),
              Group(children: [
                _InfoRow(Icons.circle, 'Status', camera.working ? 'Working' : 'Offline', tint: camera.working ? t.okInk : t.crit, small: true),
                _InfoRow(Icons.dns_outlined, 'Recorder', camera.recorder.isEmpty ? 'Not known' : camera.recorder),
                _InfoRow(Icons.lock_outline_rounded, 'Who can watch', 'Owners and managers. Every view is recorded in your history.'),
              ]),
              const SizedBox(height: 16),
              FilledButton.tonalIcon(style: FilledButton.styleFrom(minimumSize: const Size(44, 52)), onPressed: () { Navigator.of(context).pop(); onAddAlert(camera); }, icon: const Icon(Icons.add_alert_outlined), label: const Text('Add an alert for this camera')),
              // Setting the camera up (counting line, plate area, quality, pause, remove) is for the owner.
              FutureBuilder<Me>(
                future: repo.me(),
                builder: (context, me) => me.data?.role == 'admin' ? CameraSetup(repo: repo, camera: camera, onRemoved: () => Navigator.of(context).pop(true), onRenamed: (n) => setState(() => name = n)) : const SizedBox.shrink(),
              ),
            ]),
          ),
        ),
      ),
    );
  }
}
