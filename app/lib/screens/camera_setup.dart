import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../api_client.dart';
import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import '../widgets/area_editor.dart';
import '../widgets/brand.dart';
import '../widgets/count_line_editor.dart';
import 'common.dart';

/// Setting a camera up, for the owner: the counting line across the doorway, the plate area, the picture quality, pause and remove.
class CameraSetup extends StatefulWidget {
  const CameraSetup({super.key, required this.repo, required this.camera, required this.onRemoved});
  final Repository repo;
  final CameraItem camera;
  final VoidCallback onRemoved;
  @override
  State<CameraSetup> createState() => _CameraSetupState();
}

class _CameraSetupState extends State<CameraSetup> {
  late Future<CameraSettings> data = widget.repo.cameraSettings(widget.camera.id);
  Uint8List? picture;
  bool loadingPicture = false;
  CountLineController? line;
  AreaController? plate;
  bool saving = false;
  String? message;
  bool messageBad = false;

  @override
  void initState() {
    super.initState();
    _picture();
  }

  Future<void> _picture() async {
    setState(() => loadingPicture = true);
    try {
      final b = await widget.repo.snapshot(widget.camera.id);
      if (mounted) setState(() => picture = b);
    } catch (_) {/* the editor works on a plain background */}
    if (mounted) setState(() => loadingPicture = false);
  }

  void _say(String m, {bool bad = false}) => setState(() { message = m; messageBad = bad; });

  Future<void> _save(CameraSettings s) async {
    final zones = <CameraZone>[];
    final l = line;
    if (s.useFootfall && l != null) zones.add(CameraZone('count_line', [l.a, l.b], inDirection: l.inDirection));
    final p = plate;
    if (s.useAnpr && p != null && p.valid) zones.add(CameraZone('anpr_zone', p.points));
    if (zones.isEmpty) return _say('Draw the counting line or the plate area first.', bad: true);
    setState(() => saving = true);
    try {
      await widget.repo.saveZones(s.id, zones);
      line?.dirty = false;
      _say('Saved. The camera uses it within a minute.');
    } on ApiError catch (e) {
      _say(e.message, bad: true);
    }
    if (mounted) setState(() => saving = false);
  }

  Future<bool> _confirm(String title, String text, String action) async =>
      await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          title: Text(title),
          content: Text(text),
          actions: [
            TextButton(onPressed: () => Navigator.of(c).pop(false), child: const Text('Cancel')),
            FilledButton(style: FilledButton.styleFrom(backgroundColor: Tok.of(context).crit), onPressed: () => Navigator.of(c).pop(true), child: Text(action)),
          ],
        ),
      ) ??
      false;

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return FutureBuilder<CameraSettings>(
      future: data,
      builder: (context, snap) {
        if (snap.hasError) return Padding(padding: const EdgeInsets.only(top: 8), child: Text('${snap.error}', style: TextStyle(color: t.fg2)));
        if (!snap.hasData) return const Padding(padding: EdgeInsets.all(24), child: Loading());
        final s = snap.data!;
        final zl = s.zone('count_line'), zp = s.zone('anpr_zone');
        if (s.useFootfall) line ??= zl != null && zl.points.length == 2 ? CountLineController(a: zl.points[0], b: zl.points[1], flip: CountLineController.flipFor(zl.points, zl.inDirection)) : CountLineController();
        if (s.useAnpr) plate ??= AreaController(points: zp?.points, closed: (zp?.points.length ?? 0) >= 3);
        final pic = picture == null ? null : Image.memory(picture!, fit: BoxFit.cover, gaplessPlayback: true);
        return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          if (s.useFootfall && line != null) ...[
            const GroupLabel('Counting line'),
            Group(padding: EdgeInsets.all(cardPad(context)), children: [
              Text('Drag the two yellow dots to put the line across the doorway. People crossing in the direction of the arrow are counted as coming in.', style: TextStyle(color: t.fg2, height: 1.4)),
              const SizedBox(height: 10),
              CountLineEditor(controller: line!, picture: pic),
              const SizedBox(height: 10),
              Wrap(spacing: 8, runSpacing: 8, children: [
                OutlinedButton.icon(onPressed: line!.swap, icon: const Icon(Icons.swap_vert_rounded), label: const Text('Swap in / out')),
                OutlinedButton(onPressed: loadingPicture ? null : _picture, child: Text(loadingPicture ? 'Getting a picture…' : 'New picture')),
              ]),
            ]),
          ],
          if (s.useAnpr && plate != null) ...[
            const GroupLabel('Plate area'),
            Group(padding: EdgeInsets.all(cardPad(context)), children: [
              AreaEditor(controller: plate!, picture: pic, label: 'Camera picture for the plate area'),
              const SizedBox(height: 4),
              Text('Mark the part of the picture where number plates appear.', style: TextStyle(color: t.fg2, fontSize: 13)),
            ]),
          ],
          if (s.useFootfall || s.useAnpr) ...[
            const SizedBox(height: 14),
            FilledButton(onPressed: saving ? null : () => _save(s), child: Text(saving ? 'Saving…' : 'Save')),
            if (message != null) Padding(padding: const EdgeInsets.only(top: 8), child: Text(message!, style: TextStyle(color: messageBad ? t.crit : t.okInk, fontWeight: FontWeight.w600))),
          ],
          const GroupLabel('Settings'),
          Group(padding: EdgeInsets.all(cardPad(context)), children: [
            const Text('Picture quality', style: TextStyle(fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            SizedBox(
              width: double.infinity,
              child: SegmentedButton<String>(
                showSelectedIcon: false,
                segments: const [ButtonSegment(value: 'sub', label: Text('Light', maxLines: 1)), ButtonSegment(value: 'main', label: Text('Full quality', maxLines: 1))],
                selected: {s.stream},
                onSelectionChanged: (v) async {
                  try {
                    await widget.repo.setStream(s.id, v.first);
                    _say('Picture quality changed. It applies at the next reconnect.');
                    setState(() { data = widget.repo.cameraSettings(s.id); });
                  } on ApiError catch (e) {
                    _say(e.message, bad: true);
                  }
                },
              ),
            ),
            Padding(padding: const EdgeInsets.only(top: 6), child: Text('Light is enough to count people. Full quality is needed to read number plates and uses more of our servers.', style: TextStyle(color: t.fg2, fontSize: 13, height: 1.35))),
            const SizedBox(height: 14),
            Row(children: [
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: () async {
                    try {
                      await widget.repo.setCameraEnabled(s.id, !s.enabled);
                      _say(s.enabled ? 'Paused.' : 'Resumed.');
                      setState(() { data = widget.repo.cameraSettings(s.id); });
                    } on ApiError catch (e) {
                      _say(e.message, bad: true);
                    }
                  },
                  icon: Icon(s.enabled ? Icons.pause_rounded : Icons.play_arrow_rounded),
                  label: Text(s.enabled ? 'Pause this camera' : 'Resume this camera'),
                ),
              ),
            ]),
            const SizedBox(height: 10),
            TextButton.icon(
              style: TextButton.styleFrom(foregroundColor: t.crit),
              onPressed: () async {
                if (!await _confirm('Remove this camera?', 'Its past counts and plate reads stay in your reports. The channel goes back to your recorder’s list and you can add it again later.', 'Remove')) return;
                try {
                  await widget.repo.removeCamera(s.id);
                  widget.onRemoved();
                } on ApiError catch (e) {
                  _say(e.message, bad: true);
                }
              },
              icon: const Icon(Icons.delete_outline_rounded),
              label: const Text('Remove this camera'),
            ),
          ]),
        ]);
      },
    );
  }
}
