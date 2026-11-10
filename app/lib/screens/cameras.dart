import 'package:flutter/material.dart';

import '../live/live_view.dart';
import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import 'common.dart';

class CamerasPage extends StatelessWidget {
  const CamerasPage({super.key, required this.repo, required this.onAddAlert});
  final Repository repo;
  final void Function(CameraItem) onAddAlert;

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return FutureBuilder(
      future: repo.cameras(),
      builder: (context, snap) {
        if (!snap.hasData) return const Center(child: CircularProgressIndicator());
        final cams = snap.data!;
        final working = cams.where((c) => c.working).length;
        return ListView(padding: const EdgeInsets.all(16), children: [
          Text(working == cams.length ? 'All ${cams.length} cameras working' : '$working of ${cams.length} cameras working', style: TextStyle(color: t.fg2)),
          const SizedBox(height: 10),
          LayoutBuilder(builder: (context, box) {
            final cols = box.maxWidth > 900 ? 4 : box.maxWidth > 560 ? 3 : 2;
            return GridView.count(shrinkWrap: true, physics: const NeverScrollableScrollPhysics(), crossAxisCount: cols, mainAxisSpacing: 12, crossAxisSpacing: 12, childAspectRatio: 1.35, children: [
              for (final c in cams)
                InkWell(
                  borderRadius: BorderRadius.circular(16),
                  onTap: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => CameraDetail(repo: repo, camera: c, onAddAlert: onAddAlert))),
                  child: Card(
                    clipBehavior: Clip.antiAlias,
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Expanded(child: Container(width: double.infinity, color: c.working ? const Color(0xFF2E4561) : t.surface2, alignment: Alignment.topLeft, padding: const EdgeInsets.all(8), child: Pill(c.working ? 'Working' : 'Offline', c.working ? Level.ok : Level.bad))),
                      Padding(padding: const EdgeInsets.all(10), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(c.name, style: const TextStyle(fontWeight: FontWeight.w700)), Text(c.recorder, style: TextStyle(color: t.fg2, fontSize: 12.5))])),
                    ]),
                  ),
                ),
            ]);
          }),
        ]);
      },
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
      appBar: AppBar(title: Text(camera.name)),
      body: SelectionArea(child: ListView(padding: const EdgeInsets.all(16), children: [
        LiveView(repo: repo, camera: camera, compact: narrow),
        const SizedBox(height: 16),
        Card(child: Padding(padding: const EdgeInsets.all(16), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(camera.name, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
          Text('${camera.working ? 'Working' : 'Offline'} · ${camera.recorder}', style: TextStyle(color: t.fg2)),
          const SizedBox(height: 12),
          OutlinedButton(onPressed: () { Navigator.of(context).pop(); onAddAlert(camera); }, child: const Text('Add an alert for this camera')),
          const SizedBox(height: 8),
          Text('Live view is for owners and managers. Each view is recorded in your history.', style: TextStyle(color: t.fg2, fontSize: 12.5)),
        ]))),
      ])),
    );
  }
}
