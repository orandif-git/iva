import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../api_client.dart';
import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import '../widgets/brand.dart';
import 'common.dart';

String _when(DateTime? d) {
  if (d == null) return '';
  final h = d.hour % 12 == 0 ? 12 : d.hour % 12;
  const m = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return '${d.day} ${m[d.month - 1]}, $h:${d.minute.toString().padLeft(2, '0')} ${d.hour < 12 ? 'am' : 'pm'}';
}

/// Vehicles, in Activity: every number plate the cameras read in the period, newest first, with the picture taken at the time; search a plate; add one to the blocked list.
class VehiclesTab extends StatefulWidget {
  const VehiclesTab({super.key, required this.repo, this.period = Period.week, this.cameraId});
  final Repository repo;
  final Period period;
  final String? cameraId;
  @override
  State<VehiclesTab> createState() => _VehiclesTabState();
}

class _VehiclesTabState extends State<VehiclesTab> {
  final q = TextEditingController();
  final items = <PlateRead>[];
  String? next;
  bool loading = true, loadingMore = false;
  String? error;
  Timer? _debounce;
  List<CameraItem> cams = [];

  @override
  void initState() {
    super.initState();
    widget.repo.cameras().then((c) { if (mounted) setState(() => cams = c); }).catchError((_) {});
    _load(reset: true);
  }

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }

  Future<void> _load({required bool reset}) async {
    setState(() { if (reset) { loading = true; items.clear(); next = null; } else { loadingMore = true; } error = null; });
    try {
      final p = await widget.repo.plates(q: q.text, cursor: reset ? null : next, period: widget.period, cameraId: widget.cameraId);
      if (!mounted) return;
      setState(() { items.addAll(p.items); next = p.next; });
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    } catch (_) {
      if (mounted) setState(() => error = 'We could not load the plates.');
    }
    if (mounted) setState(() { loading = false; loadingMore = false; });
  }

  Future<void> _open(PlateRead p) async {
    await Navigator.of(context).push<void>(MaterialPageRoute(builder: (_) => PlateDetail(repo: widget.repo, read: p, cameraName: cams.where((c) => c.id == p.cameraId).firstOrNull?.name)));
    if (mounted) _load(reset: true);                 // the plate may have been added to or taken off the blocked list
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, navClear), children: [
      // search and the blocked list on one line
      Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
        Expanded(
          child: TextField(
            controller: q,
            maxLength: 20,
            textCapitalization: TextCapitalization.characters,
            decoration: const InputDecoration(prefixIcon: Icon(Icons.search_rounded), hintText: 'Search a plate, for example GJ14', counterText: ''),
            onChanged: (_) {
              _debounce?.cancel();
              _debounce = Timer(const Duration(milliseconds: 350), () => _load(reset: true));
            },
          ),
        ),
        const SizedBox(width: 8),
        IconButton.filledTonal(
          tooltip: 'Blocked list',
          style: IconButton.styleFrom(minimumSize: const Size(48, 48)),
          icon: const Icon(Icons.block_rounded),
          onPressed: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => BlockedListPage(repo: widget.repo))),
        ),
      ]),
      const SizedBox(height: 12),
      if (error != null)
        EmptyState(icon: Icons.cloud_off_outlined, title: 'We could not load the plates', text: error!, action: OutlinedButton(onPressed: () => _load(reset: true), child: const Text('Try again')))
      else if (loading)
        const Padding(padding: EdgeInsets.all(40), child: Loading())
      else if (items.isEmpty)
        EmptyState(icon: Icons.directions_car_outlined, title: q.text.isEmpty ? 'No vehicles read' : 'No plates match', text: q.text.isEmpty ? 'No plates were read in this period. Plates appear here with the picture taken at the time. Turn on plate reading when you add a camera at a gate.' : 'Try fewer letters, for example just GJ14.')
      else ...[
        Group(children: [
          for (final p in items)
            InkWell(
              onTap: () => _open(p),
              child: Padding(
                padding: EdgeInsets.fromLTRB(cardPad(context), 10, 10, 10),
                child: Row(children: [
                  _Thumb(repo: widget.repo, read: p),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(p.plate ?? 'unreadable', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 16, letterSpacing: 1, color: p.blocked ? t.crit : t.fg)),
                      Text([_when(p.seen), if (p.direction != null) (p.direction == 'in' ? 'In' : 'Out'), if (p.vehicle != null) p.vehicle!].where((x) => x.isNotEmpty).join(' · '), style: TextStyle(color: t.fg2, fontSize: 13)),
                      if (p.blocked || p.needsReview) Padding(padding: const EdgeInsets.only(top: 4), child: p.blocked ? const Pill('Blocked list', Level.bad) : const Pill('Needs a check', Level.warn)),
                    ]),
                  ),
                  Icon(Icons.chevron_right_rounded, color: t.fg2.withValues(alpha: .6)),
                ]),
              ),
            ),
        ]),
        if (next != null) Padding(padding: const EdgeInsets.only(top: 12), child: Center(child: OutlinedButton(onPressed: loadingMore ? null : () => _load(reset: false), child: Text(loadingMore ? 'Loading…' : 'Show more')))),
      ],
    ]);
  }
}

/// The picture taken when the plate was read, small, in the list.
class _Thumb extends StatefulWidget {
  const _Thumb({required this.repo, required this.read});
  final Repository repo;
  final PlateRead read;
  @override
  State<_Thumb> createState() => _ThumbState();
}

class _ThumbState extends State<_Thumb> {
  late final Future<Uint8List?> picture = widget.read.hasPicture ? widget.repo.plateImage(widget.read.id).catchError((_) => null) : Future.value(null);

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return ClipRRect(
      borderRadius: BorderRadius.circular(10),
      child: SizedBox(
        width: 76,
        height: 54,
        child: FutureBuilder<Uint8List?>(
          future: picture,
          builder: (context, s) => s.data != null
              ? Image.memory(s.data!, fit: BoxFit.cover, gaplessPlayback: true, excludeFromSemantics: true, errorBuilder: (_, __, ___) => _fallback(t))
              : _fallback(t),
        ),
      ),
    );
  }

  Widget _fallback(Tok t) => Container(color: widget.read.blocked ? t.critSoft : t.surface2, alignment: Alignment.center, child: Icon(Icons.directions_car_outlined, color: widget.read.blocked ? t.crit : t.fg2));
}

/// One plate read: the picture taken at the time, what is known about it, and (for the owner) putting it on the blocked list.
class PlateDetail extends StatefulWidget {
  const PlateDetail({super.key, required this.repo, required this.read, this.cameraName});
  final Repository repo;
  final PlateRead read;
  final String? cameraName;
  @override
  State<PlateDetail> createState() => _PlateDetailState();
}

class _PlateDetailState extends State<PlateDetail> {
  late final Future<Uint8List?> picture = widget.read.hasPicture ? widget.repo.plateImage(widget.read.id) : Future.value(null);
  late Future<(Me, List<BlockedPlate>)> who = _who();
  String? message;
  bool bad = false, busy = false;

  Future<(Me, List<BlockedPlate>)> _who() async {
    final r = await Future.wait([widget.repo.me(), widget.repo.blockedPlates().catchError((_) => <BlockedPlate>[])]);
    return (r[0] as Me, r[1] as List<BlockedPlate>);
  }

  Future<void> _block() async {
    final note = TextEditingController();
    final go = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text('Block ${widget.read.plate}?'),
        content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Text('From now on you are told whenever this vehicle is seen.'),
          const SizedBox(height: 12),
          TextField(controller: note, maxLength: 200, decoration: const InputDecoration(labelText: 'Note (optional)', hintText: 'For example: blocked the gate')),
        ]),
        actions: [TextButton(onPressed: () => Navigator.of(c).pop(false), child: const Text('Cancel')), FilledButton(onPressed: () => Navigator.of(c).pop(true), child: const Text('Add to blocked list'))],
      ),
    );
    if (go != true) return;
    setState(() { busy = true; message = null; });
    try {
      await widget.repo.blockPlate(widget.read.plate!, note: note.text);
      setState(() { message = '${widget.read.plate} is on the blocked list.'; bad = false; who = _who(); });
    } on ApiError catch (e) {
      setState(() { message = e.message; bad = true; });
    }
    if (mounted) setState(() => busy = false);
  }

  Future<void> _unblock(BlockedPlate e) async {
    setState(() { busy = true; message = null; });
    try {
      await widget.repo.unblockPlate(e.id);
      setState(() { message = '${e.plate} is off the blocked list.'; bad = false; who = _who(); });
    } on ApiError catch (x) {
      setState(() { message = x.message; bad = true; });
    }
    if (mounted) setState(() => busy = false);
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final p = widget.read;
    Widget row(String k, String v) => Padding(padding: EdgeInsets.fromLTRB(cardPad(context), 12, cardPad(context), 12), child: Row(children: [SizedBox(width: 110, child: Text(k, style: TextStyle(color: t.fg2))), Expanded(child: Text(v, style: const TextStyle(fontWeight: FontWeight.w600)))]));
    return Scaffold(
      appBar: AppBar(title: const Text('Number plate'), bottom: PreferredSize(preferredSize: const Size.fromHeight(1), child: Divider(height: 1, color: t.line))),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 700),
          child: FutureBuilder<(Me, List<BlockedPlate>)>(
            future: who,
            builder: (context, snap) {
              final admin = snap.data?.$1.role == 'admin';
              final entry = snap.data?.$2.where((b) => b.plate == p.plate).firstOrNull;
              final blocked = entry != null || (p.blocked && snap.data == null);
              return ListView(padding: const EdgeInsets.all(16), children: [
                ClipRRect(
                  borderRadius: BorderRadius.circular(16),
                  child: AspectRatio(
                    aspectRatio: 16 / 9,
                    child: FutureBuilder<Uint8List?>(
                      future: picture,
                      builder: (context, s) => s.data != null
                          ? Image.memory(s.data!, fit: BoxFit.cover)
                          : Container(color: t.surface2, alignment: Alignment.center, child: Text(!p.hasPicture ? 'No picture was saved for this read.' : (s.connectionState == ConnectionState.waiting ? 'Getting the picture…' : 'The picture is no longer stored.'), style: TextStyle(color: t.fg2))),
                    ),
                  ),
                ),
                const SizedBox(height: 16),
                Center(child: Text(p.plate ?? 'unreadable', style: TextStyle(fontSize: 28, fontWeight: FontWeight.w800, letterSpacing: 2, color: blocked ? t.crit : t.fg))),
                if (blocked) const Padding(padding: EdgeInsets.only(top: 8), child: Center(child: Pill('Blocked list', Level.bad))),
                if (p.needsReview) Padding(padding: const EdgeInsets.only(top: 12), child: Text('The reader was not sure about this plate.', textAlign: TextAlign.center, style: TextStyle(color: t.warnInk, fontWeight: FontWeight.w600))),
                const SizedBox(height: 14),
                Group(children: [
                  row('Seen', p.seen == null ? '—' : _when(p.seen)),
                  row('Camera', widget.cameraName ?? '—'),
                  row('Direction', p.direction == null ? '—' : (p.direction == 'in' ? 'In' : 'Out')),
                  row('Vehicle', p.vehicle ?? '—'),
                  row('Confidence', p.confidence == null ? '—' : '${p.confidence}%'),
                  if (p.dwellMinutes != null) row('Time inside', '${p.dwellMinutes} min'),
                  if (entry?.note != null && entry!.note!.isNotEmpty) row('Note', entry.note!),
                ]),
                if (admin && p.plate != null) ...[
                  const SizedBox(height: 16),
                  if (!blocked)
                    FilledButton.icon(style: FilledButton.styleFrom(backgroundColor: t.crit), onPressed: busy ? null : _block, icon: const Icon(Icons.block_rounded), label: const Text('Add to blocked list'))
                  else if (entry != null)
                    OutlinedButton.icon(onPressed: busy ? null : () => _unblock(entry), icon: const Icon(Icons.check_circle_outline_rounded), label: const Text('Remove from blocked list')),
                ],
                if (message != null) Padding(padding: const EdgeInsets.only(top: 12), child: Text(message!, textAlign: TextAlign.center, style: TextStyle(color: bad ? t.crit : t.okInk, fontWeight: FontWeight.w600))),
              ]);
            },
          ),
        ),
      ),
    );
  }
}

/// The blocked list: plates you want to hear about the moment a camera sees them. The owner can add a plate by hand or take one off.
class BlockedListPage extends StatefulWidget {
  const BlockedListPage({super.key, required this.repo});
  final Repository repo;
  @override
  State<BlockedListPage> createState() => _BlockedListPageState();
}

class _BlockedListPageState extends State<BlockedListPage> {
  late Future<(Me, List<BlockedPlate>)> data = _load();

  Future<(Me, List<BlockedPlate>)> _load() async {
    final r = await Future.wait([widget.repo.me(), widget.repo.blockedPlates()]);
    return (r[0] as Me, r[1] as List<BlockedPlate>);
  }

  void _reload() => setState(() { data = _load(); });

  Future<void> _add() async {
    final plate = TextEditingController(), note = TextEditingController();
    String? error;
    bool busy = false;
    final added = await showDialog<bool>(
      context: context,
      builder: (c) => StatefulBuilder(
        builder: (c, setD) => AlertDialog(
          title: const Text('Add a plate'),
          content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            TextField(controller: plate, autofocus: true, maxLength: 20, textCapitalization: TextCapitalization.characters, decoration: const InputDecoration(labelText: 'Plate number', hintText: 'GJ14AW1013')),
            TextField(controller: note, maxLength: 200, decoration: const InputDecoration(labelText: 'Note (optional)')),
            if (error != null) Padding(padding: const EdgeInsets.only(top: 6), child: Text(error!, style: TextStyle(color: Tok.of(c).crit, fontWeight: FontWeight.w600))),
          ]),
          actions: [
            TextButton(onPressed: () => Navigator.of(c).pop(false), child: const Text('Cancel')),
            FilledButton(
              onPressed: busy
                  ? null
                  : () async {
                      setD(() { busy = true; error = null; });
                      try {
                        await widget.repo.blockPlate(plate.text, note: note.text);
                        if (c.mounted) Navigator.of(c).pop(true);
                      } on ApiError catch (e) {
                        setD(() { busy = false; error = e.message; });
                      }
                    },
              child: Text(busy ? 'Adding…' : 'Add to blocked list'),
            ),
          ],
        ),
      ),
    );
    if (added == true) _reload();
  }

  Future<void> _remove(BlockedPlate b) async {
    final go = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text('Take ${b.plate} off the list?'),
        content: const Text('You will no longer be told when this vehicle is seen.'),
        actions: [TextButton(onPressed: () => Navigator.of(c).pop(false), child: const Text('Cancel')), FilledButton(onPressed: () => Navigator.of(c).pop(true), child: const Text('Take it off'))],
      ),
    );
    if (go != true) return;
    try {
      await widget.repo.unblockPlate(b.id);
      _reload();
    } on ApiError catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Blocked list'), bottom: PreferredSize(preferredSize: const Size.fromHeight(1), child: Divider(height: 1, color: t.line))),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 700),
          child: FutureBuilder<(Me, List<BlockedPlate>)>(
            future: data,
            builder: (context, snap) {
              if (snap.hasError) return EmptyState(icon: Icons.cloud_off_outlined, title: 'We could not load the list', text: '${snap.error}', action: OutlinedButton(onPressed: _reload, child: const Text('Try again')));
              if (!snap.hasData) return const Loading();
              final (me, list) = snap.data!;
              final admin = me.role == 'admin';
              return ListView(padding: const EdgeInsets.fromLTRB(16, 12, 16, 40), children: [
                Padding(padding: const EdgeInsets.fromLTRB(4, 0, 4, 12), child: Text('You are told, on WhatsApp, whenever a camera reads one of these plates. Turn the "Blocked vehicle seen" alert on in AI Watch.', style: TextStyle(color: t.fg2, height: 1.4))),
                if (admin) FilledButton.icon(onPressed: _add, icon: const Icon(Icons.add_rounded), label: const Text('Add a plate')),
                const SizedBox(height: 12),
                if (list.isEmpty)
                  const EmptyState(icon: Icons.block_rounded, title: 'No plates are blocked', text: 'Add a plate here, or open a vehicle in Vehicles and choose Add to blocked list.')
                else
                  Group(children: [
                    for (final b in list)
                      Padding(
                        padding: EdgeInsets.fromLTRB(cardPad(context), 10, 6, 10),
                        child: Row(children: [
                          Badge2(Icons.block_rounded, Level.bad, size: 40),
                          const SizedBox(width: 14),
                          Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                            Text(b.plate, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16, letterSpacing: 1)),
                            Text([if (b.note != null && b.note!.isNotEmpty) b.note!, if (b.added != null) 'Added ${_when(b.added)}'].join(' · '), style: TextStyle(color: t.fg2, fontSize: 13)),
                          ])),
                          if (admin) IconButton(tooltip: 'Take ${b.plate} off the list', icon: Icon(Icons.delete_outline_rounded, color: t.fg2), onPressed: () => _remove(b)),
                        ]),
                      ),
                  ]),
              ]);
            },
          ),
        ),
      ),
    );
  }
}
