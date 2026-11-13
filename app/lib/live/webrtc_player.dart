import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_webrtc/flutter_webrtc.dart' as rtc;

import '../repo.dart';
import '../theme.dart';

/// Smooth live video through WebRTC (go2rtc on the server), with one hard rule: if anything at all goes wrong the caller is
/// told to carry on with refreshed pictures. A customer never loses a camera because the smoother path had a bad day.
///
/// The offer is gathered fully before it is sent (go2rtc answers in one reply, not trickle by trickle), and the answer is put
/// on the connection. Once a frame arrives the picture is shown; a failed connection asks the parent for the fallback.
class LiveWebRtc extends StatefulWidget {
  const LiveWebRtc({super.key, required this.repo, required this.session, required this.onFallback});
  final Repository repo;
  final LiveSession session;
  final VoidCallback onFallback;

  @override
  State<LiveWebRtc> createState() => _LiveWebRtcState();
}

class _LiveWebRtcState extends State<LiveWebRtc> {
  final _renderer = rtc.RTCVideoRenderer();
  rtc.RTCPeerConnection? _pc;
  bool _done = false;          // the fallback was asked for, or the widget is gone: do nothing else

  @override
  void initState() {
    super.initState();
    _connect();
  }

  @override
  void dispose() {
    _done = true;
    _renderer.srcObject = null;
    _renderer.dispose();
    _pc?.close();
    _pc?.dispose();
    super.dispose();
  }

  void _fallBack() {
    if (_done) return;
    _done = true;
    widget.onFallback();
  }

  Future<void> _connect() async {
    try {
      await _renderer.initialize();
      if (_done) return;
      final pc = await rtc.createPeerConnection({
        'iceServers': [for (final a in widget.session.ice) {'urls': a}],
        'sdpSemantics': 'unified-plan',
      });
      _pc = pc;
      pc.onTrack = (event) {
        if (_done || event.streams.isEmpty) return;
        _renderer.srcObject = event.streams.first;
        if (mounted) setState(() {});
      };
      pc.onConnectionState = (state) {
        if (state == rtc.RTCPeerConnectionState.RTCPeerConnectionStateFailed ||
            state == rtc.RTCPeerConnectionState.RTCPeerConnectionStateClosed) {
          _fallBack();
        }
      };
      await pc.addTransceiver(kind: rtc.RTCRtpMediaType.RTCRtpMediaTypeVideo, init: rtc.RTCRtpTransceiverInit(direction: rtc.TransceiverDirection.RecvOnly));
      await pc.setLocalDescription(await pc.createOffer());
      await _gathered(pc);
      final local = await pc.getLocalDescription();
      final answer = await widget.repo.liveOffer(widget.session, local?.sdp ?? '');
      if (_done) return;
      if (answer == null || !answer.trimLeft().startsWith('v=')) {
        _fallBack();
        return;
      }
      await pc.setRemoteDescription(rtc.RTCSessionDescription(answer, 'answer'));
    } catch (_) {
      // No plugin, no network, a refused answer: the picture stream is the customer's view.
      _fallBack();
    }
  }

  /// Waits for the ICE candidates to be in the offer (at most two seconds; keep watching is more important than a perfect list).
  Future<void> _gathered(rtc.RTCPeerConnection pc) async {
    for (var i = 0; i < 20; i++) {
      if (await pc.getIceGatheringState() == rtc.RTCIceGatheringState.RTCIceGatheringStateComplete) return;
      await Future<void>.delayed(const Duration(milliseconds: 100));
    }
  }

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<rtc.RTCVideoValue>(
      valueListenable: _renderer,
      builder: (context, value, _) => value.renderVideo
          ? rtc.RTCVideoView(_renderer, objectFit: rtc.RTCVideoViewObjectFit.RTCVideoViewObjectFitCover, mirror: false)
          : ColoredBox(color: Tok.of(context).brandDeep),
    );
  }
}
