import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api_client.dart';
import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import 'common.dart';
import '../widgets/motion.dart';

/// A value to type into the recorder, with a Copy button.
class CopyRow extends StatelessWidget {
  const CopyRow(this.label, this.value, {super.key, this.secret = false});
  final String label, value;
  final bool secret;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Padding(
      padding: EdgeInsets.fromLTRB(cardPad(context), 8, 8, 8),
      child: Row(children: [
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label, style: TextStyle(color: t.fg2, fontSize: 12.5)),
          SelectableText(value, style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5, color: secret ? t.warnInk : t.fg)),
        ])),
        IconButton(
          tooltip: 'Copy $label',
          icon: Icon(Icons.copy_rounded, size: 20, color: t.brand),
          onPressed: () {
            Clipboard.setData(ClipboardData(text: value));
            ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$label copied')));
          },
        ),
      ]),
    );
  }
}

/// What to type into the recorder, in the order the recorder's own menus ask for it.
class RecorderSteps extends StatelessWidget {
  const RecorderSteps({super.key});
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    Widget step(int n, List<InlineSpan> words) => Padding(
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Container(width: 24, height: 24, alignment: Alignment.center, decoration: BoxDecoration(color: t.brandSoft, shape: BoxShape.circle), child: Text('$n', style: TextStyle(color: t.brand, fontWeight: FontWeight.w700, fontSize: 13))),
            const SizedBox(width: 12),
            Expanded(child: Text.rich(TextSpan(style: TextStyle(color: t.fg, fontSize: 14.5, height: 1.4), children: words))),
          ]),
        );
    const b = TextStyle(fontWeight: FontWeight.w700);
    return Padding(
      padding: EdgeInsets.all(cardPad(context)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        step(1, const [TextSpan(text: 'On the recorder, open '), TextSpan(text: 'Network › Register', style: b), TextSpan(text: ' (called Auto Register on some models).')]),
        step(2, const [TextSpan(text: 'Switch it on. Enter the server address and port, and set '), TextSpan(text: 'Sub-Device ID', style: b), TextSpan(text: ' exactly as above. Save.')]),
        step(3, const [TextSpan(text: 'Open '), TextSpan(text: 'Account', style: b), TextSpan(text: '. Add a user with the viewer name and password above, with '), TextSpan(text: 'live view only', style: b), TextSpan(text: '.')]),
      ]),
    );
  }
}

const _checkLabels = [
  ('reached', 'Our server heard from your recorder'),
  ('key_ok', 'The ID and password matched'),
  ('channels_found', 'We found your cameras'),
  ('preview_ok', 'We received a picture'),
];

/// The four ticks, updating themselves while the person types into the recorder.
class ConnectChecks extends StatelessWidget {
  const ConnectChecks({super.key, required this.check});
  final RecorderCheck? check;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Padding(
      padding: EdgeInsets.all(cardPad(context)),
      child: Column(children: [
        for (final (key, label) in _checkLabels)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 6),
            child: Row(children: [
              AnimatedContainer(
                duration: const Duration(milliseconds: 250),
                width: 24,
                height: 24,
                decoration: BoxDecoration(shape: BoxShape.circle, color: check?.checks[key] == true ? t.okInk : Colors.transparent, border: Border.all(color: check?.checks[key] == true ? t.okInk : t.line, width: 2)),
                child: check?.checks[key] == true ? const Icon(Icons.check_rounded, size: 16, color: Colors.white) : null,
              ),
              const SizedBox(width: 12),
              Expanded(child: Text(label, style: TextStyle(fontWeight: check?.checks[key] == true ? FontWeight.w600 : FontWeight.w500, color: check?.checks[key] == true ? t.fg : t.fg2))),
            ]),
          ),
      ]),
    );
  }
}

/// The recorder's channels: the customer picks which become cameras.
class ChannelsPanel extends StatefulWidget {
  const ChannelsPanel({super.key, required this.repo, required this.recorderId, required this.channels, required this.onAdded, this.canAdd = true});
  final Repository repo;
  final String recorderId;
  final List<Channel> channels;
  final VoidCallback onAdded;
  final bool canAdd;
  @override
  State<ChannelsPanel> createState() => _ChannelsPanelState();
}

class _ChannelsPanelState extends State<ChannelsPanel> {
  final names = <int, TextEditingController>{};
  final busy = <int>{};
  String? error;
  bool plates = false;

  TextEditingController _name(Channel c) => names.putIfAbsent(c.no, () => TextEditingController(text: c.label));

  Future<void> _add(Channel c) async {
    setState(() { busy.add(c.no); error = null; });
    try {
      await widget.repo.addCamera(widget.recorderId, c.no, _name(c).text.trim().isEmpty ? c.label : _name(c).text.trim(), people: true, plates: plates);
      widget.onAdded();
    } on ApiError catch (e) {
      error = e.message;
    }
    if (mounted) setState(() => busy.remove(c.no));
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final cams = widget.channels.where((c) => c.active || c.enabled).toList();
    if (cams.isEmpty) return Padding(padding: EdgeInsets.all(cardPad(context)), child: Text('No cameras found on this recorder yet.', style: TextStyle(color: t.fg2)));
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      for (final c in cams)
        Padding(
          padding: EdgeInsets.fromLTRB(cardPad(context), 10, 12, 10),
          child: Row(children: [
            Badge2(Icons.videocam_outlined, c.enabled ? Level.ok : Level.mute, size: 40),
            const SizedBox(width: 12),
            Expanded(
              child: c.enabled
                  ? Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(c.label, style: const TextStyle(fontWeight: FontWeight.w700)), Text('Channel ${c.no} · added', style: TextStyle(color: t.fg2, fontSize: 13))])
                  : TextField(controller: _name(c), decoration: InputDecoration(labelText: 'Channel ${c.no} name', isDense: true)),
            ),
            const SizedBox(width: 8),
            if (c.enabled)
              const Pill('Added', Level.ok)
            else if (widget.canAdd)
              FilledButton(style: FilledButton.styleFrom(minimumSize: const Size(64, 40)), onPressed: busy.contains(c.no) ? null : () => _add(c), child: Text(busy.contains(c.no) ? 'Adding…' : 'Add')),
          ]),
        ),
      if (widget.canAdd && cams.any((c) => !c.enabled))
        InkWell(
          onTap: () => setState(() => plates = !plates),
          child: Padding(
            padding: EdgeInsets.fromLTRB(cardPad(context) - 8, 2, cardPad(context), 6),
            child: Row(children: [
              Checkbox(value: plates, onChanged: (v) => setState(() => plates = v ?? false), visualDensity: VisualDensity.compact),
              const Expanded(child: Text('Also read number plates (if your plan includes it)', style: TextStyle(fontSize: 14))),
            ]),
          ),
        ),
      if (error != null) Padding(padding: EdgeInsets.fromLTRB(cardPad(context), 0, cardPad(context), 10), child: Text(error!, style: TextStyle(color: t.crit, fontWeight: FontWeight.w600, fontSize: 13))),
    ]);
  }
}

/// Add a recorder: a name, then the values to type in, then the checks tick while you do.
class RecorderSetupPage extends StatefulWidget {
  const RecorderSetupPage({super.key, required this.repo, this.pollEvery = const Duration(seconds: 3)});
  final Repository repo;
  final Duration pollEvery;
  @override
  State<RecorderSetupPage> createState() => _RecorderSetupPageState();
}

class _RecorderSetupPageState extends State<RecorderSetupPage> {
  final name = TextEditingController();
  RecorderSetup? setup;
  RecorderCheck? check;
  List<Channel> channels = [];
  bool busy = false;
  String? error;
  Timer? timer;

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  Future<void> _create() async {
    setState(() { busy = true; error = null; });
    try {
      final s = await widget.repo.addRecorder(name.text);
      setState(() { setup = s; busy = false; });
      timer = Timer.periodic(widget.pollEvery, (_) => _poll());
      _poll();
    } on ApiError catch (e) {
      setState(() { busy = false; error = e.message; });
    }
  }

  Future<void> _poll() async {
    final s = setup;
    if (s == null) return;
    try {
      final c = await widget.repo.recorderStatus(s.id);
      final ch = c.channelsFound ? await widget.repo.channels(s.id) : channels;
      if (!mounted) return;
      setState(() { check = c; channels = ch; });
      if (c.done) timer?.cancel();
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final s = setup;
    return Scaffold(
      appBar: AppBar(title: const Text('Connect a recorder'), bottom: PreferredSize(preferredSize: const Size.fromHeight(1), child: Divider(height: 1, color: t.line))),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 680),
          child: ListView(padding: const EdgeInsets.fromLTRB(16, 12, 16, 40), children: [
            if (s == null) ...[
              Padding(padding: const EdgeInsets.fromLTRB(4, 4, 4, 14), child: Text('It takes about ten minutes. You type a few details into your recorder and we do the rest. Nobody needs to visit your shop.', style: TextStyle(color: t.fg2, height: 1.4))),
              TextField(controller: name, maxLength: 120, decoration: const InputDecoration(labelText: 'Name (optional)', helperText: 'For example Shop recorder or Godown')),
              if (error != null) Padding(padding: const EdgeInsets.only(top: 8), child: Text(error!, style: TextStyle(color: t.crit, fontWeight: FontWeight.w600))),
              const SizedBox(height: 14),
              FilledButton(onPressed: busy ? null : _create, child: Text(busy ? 'Creating…' : 'Continue')),
            ] else ...[
              const GroupLabel('1. Type these into your recorder'),
              Group(children: [
                CopyRow('Server address', s.server),
                CopyRow('Port', '${s.port}'),
                CopyRow('Sub-Device ID', s.deviceId),
                CopyRow('Viewer user', s.viewerUser),
                CopyRow('Viewer password', s.key ?? '', secret: true),
              ]),
              Padding(padding: const EdgeInsets.fromLTRB(6, 8, 6, 0), child: Text('The password is shown only now. Copy it before you leave this page.', style: TextStyle(color: t.warnInk, fontSize: 13, fontWeight: FontWeight.w600))),
              const GroupLabel('2. On the recorder'),
              const Group(children: [RecorderSteps()]),
              GroupLabel(check?.done == true ? 'Connected' : 'This page updates itself'),
              Group(children: [ConnectChecks(check: check)]),
              if (check?.channelsFound == true) ...[
                const GroupLabel('3. Choose your cameras'),
                Group(children: [ChannelsPanel(repo: widget.repo, recorderId: s.id, channels: channels, onAdded: _poll)]),
              ],
              const SizedBox(height: 18),
              FilledButton(onPressed: () => Navigator.of(context).pop(true), child: Text(check?.done == true ? 'Done' : 'Finish later')),
            ],
          ]),
        ),
      ),
    );
  }
}

/// One recorder: its state, what it was given, its cameras, and (for the owner) rename, new password and remove.
class RecorderPage extends StatefulWidget {
  const RecorderPage({super.key, required this.repo, required this.recorder, required this.canEdit});
  final Repository repo;
  final RecorderItem recorder;
  final bool canEdit;
  @override
  State<RecorderPage> createState() => _RecorderPageState();
}

class _RecorderPageState extends State<RecorderPage> {
  late Future<(RecorderSetup, RecorderCheck, List<Channel>)> data = _load();
  late final TextEditingController rename = TextEditingController(text: widget.recorder.name);
  RecorderSetup? fresh; // a new password, shown once
  bool renaming = false;
  String? note;

  Future<(RecorderSetup, RecorderCheck, List<Channel>)> _load() async {
    final r = await Future.wait([widget.repo.recorderConnection(widget.recorder.id), widget.repo.recorderStatus(widget.recorder.id), widget.repo.channels(widget.recorder.id)]);
    return (r[0] as RecorderSetup, r[1] as RecorderCheck, r[2] as List<Channel>);
  }

  void _reload() => setState(() { data = _load(); });

  Future<bool> _confirm(String title, String text, String action, {bool danger = false}) async =>
      await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          title: Text(title),
          content: Text(text),
          actions: [
            TextButton(onPressed: () => Navigator.of(c).pop(false), child: const Text('Cancel')),
            FilledButton(style: danger ? FilledButton.styleFrom(backgroundColor: Tok.of(context).crit) : null, onPressed: () => Navigator.of(c).pop(true), child: Text(action)),
          ],
        ),
      ) ??
      false;

  Future<void> _saveName() async {
    try {
      await widget.repo.renameRecorder(widget.recorder.id, rename.text);
      setState(() { renaming = false; note = 'Name saved.'; });
    } on ApiError catch (e) {
      setState(() => note = e.message);
    }
  }

  Future<void> _newKey() async {
    if (!await _confirm('Make a new password?', 'This replaces the viewer password. You must type the new one into the recorder, and the recorder stays disconnected until you do.', 'Make new password')) return;
    try {
      final k = await widget.repo.newRecorderKey(widget.recorder.id);
      setState(() => fresh = k);
    } on ApiError catch (e) {
      setState(() => note = e.message);
    }
  }

  Future<void> _remove() async {
    if (!await _confirm('Remove this recorder?', 'Its cameras, people counts and plate reads are deleted for good. This cannot be undone.', 'Remove it', danger: true)) return;
    try {
      await widget.repo.removeRecorder(widget.recorder.id);
      if (mounted) Navigator.of(context).pop(true);
    } on ApiError catch (e) {
      setState(() => note = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final r = widget.recorder;
    return Scaffold(
      appBar: AppBar(title: Text(r.name), bottom: PreferredSize(preferredSize: const Size.fromHeight(1), child: Divider(height: 1, color: t.line))),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 680),
          child: FutureBuilder(
            future: data,
            builder: (context, snap) {
              if (snap.hasError) return EmptyState(icon: Icons.cloud_off_outlined, title: 'We could not load this recorder', text: '${snap.error}', action: OutlinedButton(onPressed: _reload, child: const Text('Try again')));
              if (!snap.hasData) return const Padding(padding: EdgeInsets.all(16), child: SkeletonRows());
              final (conn, check, channels) = snap.data!;
              final online = check.status == 'online';
              return ListView(padding: const EdgeInsets.fromLTRB(16, 12, 16, 40), children: [
                Group(children: [
                  Padding(
                    padding: EdgeInsets.all(cardPad(context)),
                    child: Row(children: [
                      Badge2(Icons.dns_outlined, online ? Level.ok : (check.status == 'never' ? Level.mute : Level.bad), size: 44),
                      const SizedBox(width: 14),
                      Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text(online ? 'Connected' : (check.status == 'never' ? 'Not connected yet' : 'Offline'), style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
                        Text(online ? 'Sending pictures to us.' : 'Check the recorder is on and has internet. Its details are below.', style: TextStyle(color: t.fg2, fontSize: 13.5)),
                      ])),
                    ]),
                  ),
                ]),
                if (note != null) Padding(padding: const EdgeInsets.fromLTRB(6, 10, 6, 0), child: Text(note!, style: TextStyle(color: t.okInk, fontWeight: FontWeight.w600))),
                if (r.health != null) ...[const GroupLabel('How the recorder is doing'), RecorderHealthCard(r.health!)],
                const GroupLabel('Cameras'),
                Group(children: [ChannelsPanel(repo: widget.repo, recorderId: r.id, channels: channels, onAdded: _reload, canAdd: widget.canEdit)]),
                const GroupLabel('What is typed into the recorder'),
                Group(children: [CopyRow('Server address', conn.server), CopyRow('Port', '${conn.port}'), CopyRow('Sub-Device ID', conn.deviceId), CopyRow('Viewer user', conn.viewerUser)]),
                if (fresh != null) ...[
                  const GroupLabel('New viewer password'),
                  Group(children: [CopyRow('Viewer password', fresh!.key ?? '', secret: true)]),
                  Padding(padding: const EdgeInsets.fromLTRB(6, 8, 6, 0), child: Text('Shown only once. Type it into the recorder’s Account page for this user.', style: TextStyle(color: t.warnInk, fontSize: 13, fontWeight: FontWeight.w600))),
                ],
                if (widget.canEdit) ...[
                  const GroupLabel('Manage'),
                  Group(children: [
                    InkWell(onTap: () => setState(() => renaming = !renaming), child: Padding(padding: EdgeInsets.all(cardPad(context)), child: Row(children: [Icon(Icons.edit_outlined, color: t.fg2), const SizedBox(width: 14), const Expanded(child: Text('Rename', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15.5))), Icon(renaming ? Icons.expand_less_rounded : Icons.chevron_right_rounded, color: t.fg2)]))),
                    AnimatedSize(
                      duration: const Duration(milliseconds: 200),
                      alignment: Alignment.topCenter,
                      child: renaming
                          ? Padding(padding: EdgeInsets.fromLTRB(cardPad(context), 0, cardPad(context), 14), child: Row(children: [Expanded(child: TextField(controller: rename, decoration: const InputDecoration(labelText: 'Name', isDense: true))), const SizedBox(width: 10), FilledButton(onPressed: _saveName, child: const Text('Save'))]))
                          : const SizedBox(width: double.infinity),
                    ),
                    InkWell(onTap: _newKey, child: Padding(padding: EdgeInsets.all(cardPad(context)), child: Row(children: [Icon(Icons.key_rounded, color: t.fg2), const SizedBox(width: 14), const Expanded(child: Text('Make a new viewer password', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15.5))), Icon(Icons.chevron_right_rounded, color: t.fg2)]))),
                    InkWell(onTap: _remove, child: Padding(padding: EdgeInsets.all(cardPad(context)), child: Row(children: [Icon(Icons.delete_outline_rounded, color: t.crit), const SizedBox(width: 14), Expanded(child: Text('Remove this recorder', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15.5, color: t.crit)))]))),
                  ]),
                ],
              ]);
            },
          ),
        ),
      ),
    );
  }
}


/// What the recorder last said about itself, in plain words: are the disks all right, how full, how many days of video are kept, is its clock right. Each line is green when
/// fine, amber when it needs a look and red when it needs fixing.
class RecorderHealthCard extends StatelessWidget {
  const RecorderHealthCard(this.h, {super.key});
  final RecorderHealth h;

  String _size(int mb) => mb >= 1000000 ? '${(mb / 1000000).toStringAsFixed(mb >= 10000000 ? 0 : 1)} TB' : '${(mb / 1000).round()} GB';

  String _uptime(int s) {
    final d = s ~/ 86400;
    return d >= 2 ? 'Running for $d days' : (s >= 3600 ? 'Running for ${s ~/ 3600} hours' : 'Started ${(s ~/ 60).clamp(1, 59)} minutes ago');
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    Widget row(IconData icon, Level level, String title, String sub) {
      final (bg, fg) = levelColors(t, level);
      return Padding(
        padding: EdgeInsets.fromLTRB(cardPad(context), 12, cardPad(context), 12),
        child: Row(children: [
          Container(width: 40, height: 40, decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(12)), child: Icon(icon, color: fg, size: 21)),
          const SizedBox(width: 12),
          Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(title, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15)), Text(sub, style: TextStyle(color: t.fg2, fontSize: 12.5))])),
        ]),
      );
    }

    final faulty = h.disks.where((d) => d.faulty).toList();
    final worn = h.disks.where((d) => !d.faulty && d.lifePercent != null && d.lifePercent! <= 20).toList();
    final total = h.disks.fold<int>(0, (s, d) => s + d.totalMb), free = h.disks.fold<int>(0, (s, d) => s + d.freeMb);
    final full = h.percentFull;
    final rows = <Widget>[
      if (h.disks.isNotEmpty)
        row(Icons.storage_rounded, faulty.isNotEmpty ? Level.bad : (worn.isNotEmpty ? Level.warn : Level.ok),
            faulty.isNotEmpty ? 'A disk has a fault' : (worn.isNotEmpty ? 'A disk is wearing out' : (h.disks.length == 1 ? 'The disk is healthy' : 'All ${h.disks.length} disks are healthy')),
            faulty.isNotEmpty ? 'Have the recorder checked: video may not be saved.' : (worn.isNotEmpty ? 'About ${worn.first.lifePercent!.round()}% of its life is left. Replace it soon.' : '${_size(total)} in all')),
      if (full != null)
        // A recorder that records all the time replaces its oldest video when its disk is full, so a full disk is normal and never shown as a problem.
        row(Icons.pie_chart_outline_rounded, Level.ok, full >= 95 ? 'The disk is full, as it should be' : '$full% of the disk is in use',
            full >= 95 ? 'The recorder replaces its oldest video with the newest.' : '${_size(total - free)} used of ${_size(total)}'),
      if (h.daysHeld != null) row(Icons.history_rounded, Level.ok, 'About ${h.daysHeld} days of video kept', 'How far back you can look'),
      if (h.clockOutMinutes != null) row(Icons.schedule_rounded, h.clockOutMinutes! >= 5 ? Level.warn : Level.ok, h.clockOutMinutes! >= 5 ? 'The clock is ${h.clockOutMinutes} minutes out' : 'The clock is right', h.clockOutMinutes! >= 5 ? 'Recordings and reports show the wrong times.' : 'Recordings show the right times.'),
      if (h.uptimeSeconds != null) row(Icons.power_settings_new_rounded, Level.mute, _uptime(h.uptimeSeconds!), 'Since it last started'),
    ];
    return Group(children: [
      if (rows.isEmpty) Padding(padding: EdgeInsets.all(cardPad(context)), child: Text('The recorder has not reported yet. It is asked every ten minutes.', style: TextStyle(color: t.fg2))) else ...rows,
      if (h.checkedAt != null) Padding(padding: EdgeInsets.fromLTRB(cardPad(context), 10, cardPad(context), 10), child: Text('Checked ${shortAgo(DateTime.now().difference(h.checkedAt!).inSeconds.clamp(0, 1 << 30))}', style: TextStyle(color: t.fg2, fontSize: 12.5))),
    ]);
  }
}
