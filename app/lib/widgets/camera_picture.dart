import 'dart:async';
import 'dart:collection';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';

import '../repo.dart';

/// Camera pictures are taken from the recorder, so only two are asked for at a time however many tiles are on the screen. A page of cameras then fills in
/// a couple at a time and never floods the recorders.
class PictureQueue {
  PictureQueue._();
  static final instance = PictureQueue._();
  static const _most = 2;
  int _running = 0;
  final _waiting = Queue<Completer<void>>();

  Future<T> run<T>(Future<T> Function() job) async {
    if (_running >= _most) {
      final c = Completer<void>();
      _waiting.add(c);
      await c.future;
    } else {
      _running++;
    }
    try {
      return await job();
    } finally {
      if (_waiting.isNotEmpty) {
        _waiting.removeFirst().complete();      // hand the place straight to the next one
      } else {
        _running--;
      }
    }
  }
}

/// The camera's own latest picture, fetched again every few seconds while it is on the screen. It keeps showing the last picture while the next one is
/// on its way, stops when the tile leaves the screen or the tab is hidden, and says nothing if a picture cannot be had (the plain tile stays).
class CameraPicture extends StatefulWidget {
  const CameraPicture({super.key, required this.repo, required this.cameraId, required this.active, this.every = const Duration(seconds: 5), this.onPicture});
  final Repository repo;
  final String cameraId;
  final bool active;                    // false: the camera is offline or paused, so there is nothing to fetch
  final Duration every;
  final ValueChanged<bool>? onPicture;  // told whether a picture is showing (the tile hides its play button)

  @override
  State<CameraPicture> createState() => _CameraPictureState();
}

class _CameraPictureState extends State<CameraPicture> {
  Uint8List? bytes;
  Timer? _timer;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    if (widget.active) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _fetch());          // once the tile has a place on the screen
      _timer = Timer.periodic(widget.every, (_) => _fetch());
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  bool get _onScreen {
    final box = context.findRenderObject();
    if (box is! RenderBox || !box.attached || !box.hasSize) return false;
    final top = box.localToGlobal(Offset.zero).dy, h = MediaQuery.sizeOf(context).height;
    return top < h + 150 && top + box.size.height > -150;
  }

  Future<void> _fetch() async {
    if (_busy || !mounted || !TickerMode.of(context) || !_onScreen) return;
    _busy = true;
    try {
      final b = await PictureQueue.instance.run(() => widget.repo.snapshot(widget.cameraId));
      if (mounted && b != null && b.isNotEmpty) {
        final first = bytes == null;
        setState(() => bytes = b);
        if (first) widget.onPicture?.call(true);
      }
    } catch (_) {/* the next one will do */}
    _busy = false;
  }

  @override
  Widget build(BuildContext context) {
    final b = bytes;
    if (b == null) return const SizedBox.shrink();
    return Image.memory(b, fit: BoxFit.cover, gaplessPlayback: true, excludeFromSemantics: true, errorBuilder: (_, __, ___) => const SizedBox.shrink());
  }
}
