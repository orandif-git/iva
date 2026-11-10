import 'package:flutter/material.dart';

import '../live/live_view.dart';
import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import '../widgets/brand.dart';
import 'common.dart';

class CamerasPage extends StatelessWidget {
  const CamerasPage({super.key, required this.repo, required this.onAddAlert});
  final Repository repo;
  final void Function(CameraItem) onAddAlert;

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final wide = MediaQuery.sizeOf(context).width >= 900;
    return FutureBuilder(
      future: repo.cameras(),
      builder: (context, snap) {
        if (snap.hasError) return EmptyState(icon: Icons.cloud_off_outlined, title: 'We could not load your cameras', text: '${snap.error}');
        if (!snap.hasData) return const Loading(text: 'Loading your cameras');
        final cams = snap.data!;
        final working = cams.where((c) => c.working).length;
        if (cams.isEmpty) return const EmptyState(icon: Icons.videocam_off_outlined, title: 'No cameras yet', text: 'Connect a recorder in the current app, and your cameras will appear here.');
        return ListView(padding: EdgeInsets.fromLTRB(wide ? 32 : 16, 8, wide ? 32 : 16, navClear), children: [
          Row(children: [
            Pill(working == cams.length ? 'All ${cams.length} working' : '$working of ${cams.length} working', working == cams.length ? Level.ok : Level.warn, dot: true),
            const SizedBox(width: 10),
            Expanded(child: Text('Tap a camera for live view', style: TextStyle(color: t.fg2, fontSize: 13))),
          ]),
          const SizedBox(height: 14),
          LayoutBuilder(builder: (context, box) {
            final cols = box.maxWidth > 900 ? 4 : box.maxWidth > 600 ? 3 : 2;
            return GridView.count(shrinkWrap: true, physics: const NeverScrollableScrollPhysics(), crossAxisCount: cols, mainAxisSpacing: 14, crossAxisSpacing: 14, childAspectRatio: 1.18, children: [
              for (final c in cams) _CamCard(c, () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => CameraDetail(repo: repo, camera: c, onAddAlert: onAddAlert)))),
            ]);
          }),
        ]);
      },
    );
  }
}

class _CamCard extends StatelessWidget {
  const _CamCard(this.c, this.onTap);
  final CameraItem c;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) {
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
                Positioned.fill(child: CustomPaint(painter: FramePainter(color: (c.working ? Colors.white : t.fg2).withValues(alpha: .22), inset: 10, arm: 16, width: 2))),
                Center(child: Icon(c.working ? Icons.videocam_rounded : Icons.videocam_off_rounded, size: 34, color: (c.working ? Colors.white : t.fg2).withValues(alpha: .55))),
                Positioned(left: 10, top: 10, child: Pill(c.working ? 'Working' : 'Offline', c.working ? Level.ok : Level.bad, dot: true)),
              ]),
            ),
            Padding(padding: const EdgeInsets.fromLTRB(14, 10, 14, 12), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(c.name, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15), maxLines: 1, overflow: TextOverflow.ellipsis), Text(c.recorder, style: TextStyle(color: t.fg2, fontSize: 12.5), maxLines: 1, overflow: TextOverflow.ellipsis)])),
          ]),
        ),
      ),
    );
  }
}

/// A camera, full screen on a phone. Live view and AI view start here and nowhere else.
class CameraDetail extends StatelessWidget {
  const CameraDetail({super.key, required this.repo, required this.camera, required this.onAddAlert});
  final Repository repo;
  final CameraItem camera;
  final void Function(CameraItem) onAddAlert;

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final narrow = MediaQuery.sizeOf(context).width < 700;
    return Scaffold(
      appBar: AppBar(title: Text(camera.name), bottom: PreferredSize(preferredSize: const Size.fromHeight(1), child: Divider(height: 1, color: t.line))),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 900),
          child: SelectionArea(
            child: ListView(padding: const EdgeInsets.all(16), children: [
              LiveView(repo: repo, camera: camera, compact: narrow),
              const SizedBox(height: 16),
              Section(
                icon: Icons.videocam_outlined,
                title: camera.name,
                subtitle: '${camera.working ? 'Working' : 'Offline'} · ${camera.recorder}',
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  OutlinedButton.icon(onPressed: () { Navigator.of(context).pop(); onAddAlert(camera); }, icon: const Icon(Icons.add_alert_outlined), label: const Text('Add an alert for this camera')),
                  const SizedBox(height: 10),
                  Text('Live view is for owners and managers. Each view is recorded in your history.', style: TextStyle(color: t.fg2, fontSize: 12.5)),
                ]),
              ),
            ]),
          ),
        ),
      ),
    );
  }
}
