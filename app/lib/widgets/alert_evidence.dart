import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api_client.dart';
import '../models.dart';
import '../repo.dart';
import '../theme.dart';

/// How a link is opened: in a new browser tab, or the phone's browser. Tests swap it.
Future<bool> Function(Uri) linkOpener = (u) => launchUrl(u, mode: LaunchMode.externalApplication);

/// What the camera saw when the alert happened: the picture, and a button for the short video when there is one. The picture is fetched only for people signed in to
/// this customer; the video opens through a link made for this moment that stops working after an hour. Both disappear after the retention period set by Jas iVision.
class AlertEvidence extends StatefulWidget {
  const AlertEvidence({super.key, required this.repo, required this.alert});
  final Repository repo;
  final AlertItem alert;
  @override
  State<AlertEvidence> createState() => _AlertEvidenceState();
}

class _AlertEvidenceState extends State<AlertEvidence> {
  late final Future<Uint8List?> picture = widget.alert.hasEvidence ? widget.repo.alertImage(widget.alert.id).catchError((_) => null) : Future.value(null);
  bool opening = false;
  String? note;

  Future<void> _open() async {
    setState(() { opening = true; note = null; });
    try {
      final l = await widget.repo.evidenceLink(widget.alert.id);
      if (!await linkOpener(Uri.parse(l.url))) throw StateError('could not open');
    } on ApiError catch (e) {
      note = e.message;
    } catch (_) {
      note = 'We could not open it on this device.';
    }
    if (mounted) setState(() => opening = false);
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final a = widget.alert;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (a.hasEvidence)
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 460),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(14),
              child: AspectRatio(
                aspectRatio: 16 / 9,
                child: FutureBuilder<Uint8List?>(
                  future: picture,
                  builder: (context, s) => s.data != null
                      ? Image.memory(s.data!, fit: BoxFit.cover, gaplessPlayback: true, semanticLabel: 'What the camera saw: ${a.title}', errorBuilder: (_, __, ___) => _none(t, 'The picture could not be shown.'))
                      : _none(t, s.connectionState == ConnectionState.waiting ? 'Getting the picture…' : 'The picture is no longer stored.'),
                ),
              ),
            ),
          ),
        const SizedBox(height: 8),
        Wrap(spacing: 8, runSpacing: 4, children: [
          if (a.hasClip) FilledButton.tonalIcon(style: FilledButton.styleFrom(minimumSize: const Size(44, 40)), onPressed: opening ? null : _open, icon: const Icon(Icons.play_circle_outline_rounded), label: Text(opening ? 'Opening…' : 'Watch the video'))
          else if (a.hasEvidence) TextButton.icon(onPressed: opening ? null : _open, icon: const Icon(Icons.open_in_new_rounded, size: 18), label: Text(opening ? 'Opening…' : 'Open full size')),
        ]),
        if (note != null) Padding(padding: const EdgeInsets.only(top: 4), child: Text(note!, style: TextStyle(color: t.crit, fontWeight: FontWeight.w600, fontSize: 13))),
      ]),
    );
  }

  Widget _none(Tok t, String text) => Container(color: t.surface2, alignment: Alignment.center, padding: const EdgeInsets.all(12), child: Text(text, textAlign: TextAlign.center, style: TextStyle(color: t.fg2, fontSize: 13)));
}
