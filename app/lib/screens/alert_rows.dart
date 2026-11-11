import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import '../widgets/alert_evidence.dart';
import 'common.dart';

/// Marks every open alert in a group as done, or snoozes them, with one tap.
Future<void> doneGroup(Repository repo, AlertGroup g) async {
  for (final a in g.openItems) {
    await repo.done(a.id);
  }
}

Future<void> snoozeGroup(Repository repo, AlertGroup g, {int minutes = 60}) async {
  for (final a in g.openItems) {
    await repo.snooze(a.id, minutes: minutes);
  }
}

String _stateWord(AlertGroup g) => g.items.every((a) => a.state == AlertState.done) ? 'Done' : 'Snoozed';

/// One row for a kind of alert in one place. Repeats are folded in: "Too many people · Aisle 1 · 9 times today, last 4:29 pm".
/// Colour shows only that something is open (a small dot); the AI sparkle sits on the corner of Smart Alerts' own icon.
class AlertRow extends StatefulWidget {
  const AlertRow({super.key, required this.group, required this.repo, required this.onChanged, this.alwaysOpen = false, this.swipe = false});
  final AlertGroup group;
  final Repository repo;
  final VoidCallback onChanged;
  final bool alwaysOpen; // Home: the buttons are always visible
  final bool swipe; // phone Inbox: swipe right for Done, left for Snooze
  @override
  State<AlertRow> createState() => _AlertRowState();
}

class _AlertRowState extends State<AlertRow> {
  bool expanded = false;

  Future<void> _done() async {
    HapticFeedback.lightImpact();
    await doneGroup(widget.repo, widget.group);
    widget.onChanged();
  }

  Future<void> _snooze() async {
    HapticFeedback.lightImpact();
    await snoozeGroup(widget.repo, widget.group);
    widget.onChanged();
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final g = widget.group, a = g.latest;
    final open = g.open;
    final show = widget.alwaysOpen || expanded;
    final narrow = MediaQuery.sizeOf(context).width < 600;
    final sidePad = widget.alwaysOpen ? 0.0 : cardPad(context);
    final head = InkWell(
      onTap: widget.alwaysOpen ? null : () => setState(() => expanded = !expanded),
      child: Padding(
        padding: EdgeInsets.fromLTRB(sidePad, 14, sidePad, show ? 6 : 14),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Badge2(iconForType(a.rule, smart: a.smart), a.smart ? Level.ai : (open ? Level.bad : Level.mute), ai: a.smart, size: 42),
          const SizedBox(width: 14),
          Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(a.title, style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5, color: open ? t.fg : t.fg2)),
            const SizedBox(height: 2),
            Row(children: [
              Flexible(child: Text(repeatsLine(g), style: TextStyle(color: t.fg2, fontSize: 13))),
              if (a.hasEvidence || a.hasClip) Padding(padding: const EdgeInsets.only(left: 8), child: Semantics(label: a.hasClip ? 'Has a picture and a video' : 'Has a picture', child: Icon(a.hasClip ? Icons.videocam_outlined : Icons.photo_camera_outlined, size: 16, color: t.fg2))),
            ]),
          ])),
          const SizedBox(width: 10),
          if (open) Padding(padding: const EdgeInsets.only(top: 6), child: Semantics(label: 'Open', child: Container(width: 10, height: 10, decoration: BoxDecoration(color: t.crit, shape: BoxShape.circle)))) else Pill(_stateWord(g), Level.mute),
          if (!widget.alwaysOpen) Padding(padding: const EdgeInsets.only(left: 4), child: Icon(expanded ? Icons.expand_less_rounded : Icons.expand_more_rounded, color: t.fg2, size: 22)),
        ]),
      ),
    );
    final detail = !show
        ? const SizedBox.shrink()
        : Padding(
            padding: EdgeInsets.fromLTRB(sidePad + 56, 0, sidePad, 14),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              if (!widget.alwaysOpen && a.body.isNotEmpty) Padding(padding: const EdgeInsets.only(bottom: 8), child: Text(a.body, style: const TextStyle(height: 1.4))),
              if (!widget.alwaysOpen && (a.hasEvidence || a.hasClip)) AlertEvidence(repo: widget.repo, alert: a),
              if (!widget.alwaysOpen && g.count > 1) Padding(padding: const EdgeInsets.only(bottom: 10), child: Text('Also at ${g.items.skip(1).take(5).map((x) => x.when).join(' · ')}${g.count > 6 ? ' · and ${g.count - 6} more' : ''}', style: TextStyle(color: t.fg2, fontSize: 12.5))),
              if (open)
                Wrap(spacing: 8, runSpacing: 8, children: [
                  FilledButton.tonal(style: FilledButton.styleFrom(minimumSize: const Size(44, 42)), onPressed: _done, child: Text(g.openItems.length > 1 ? 'Mark all done (${g.openItems.length})' : 'Done')),
                  TextButton(onPressed: _snooze, child: const Text('Snooze')),
                ]),
            ]),
          );
    final row = Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [head, detail]);
    if (!widget.swipe || !open || !narrow) return row;
    return Dismissible(
      key: ValueKey('swipe-${g.key}-${g.latest.id}'),
      background: Container(color: t.okSoft, alignment: Alignment.centerLeft, padding: const EdgeInsets.only(left: 22), child: Row(mainAxisSize: MainAxisSize.min, children: [Icon(Icons.check_rounded, color: t.okInk), const SizedBox(width: 8), Text('Done', style: TextStyle(color: t.okInk, fontWeight: FontWeight.w700))])),
      secondaryBackground: Container(color: t.warnSoft, alignment: Alignment.centerRight, padding: const EdgeInsets.only(right: 22), child: Row(mainAxisSize: MainAxisSize.min, children: [Text('Snooze', style: TextStyle(color: t.warnInk, fontWeight: FontWeight.w700)), const SizedBox(width: 8), Icon(Icons.snooze_rounded, color: t.warnInk)])),
      confirmDismiss: (dir) async {
        if (dir == DismissDirection.startToEnd) {
          await _done();
        } else {
          await _snooze();
        }
        return false; // the list reloads and the row moves on its own
      },
      child: row,
    );
  }
}
