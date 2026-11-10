import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import 'live/live_rules.dart';
import 'models.dart';

class LiveSession {
  final String id;
  final LiveMode mode;
  const LiveSession(this.id, this.mode);
}

/// Everything the screens need from the server. [ApiRepository] talks to the real backend; [MockRepository] runs the same screens with demo data.
abstract class Repository {
  Future<Health> health();
  Future<List<AlertItem>> alerts();
  Future<void> done(String alertId);
  Future<void> snooze(String alertId, {int minutes = 60});
  Future<List<CameraItem>> cameras();
  Future<LiveSession> startLive(String cameraId, LiveMode mode);
  Future<void> extendLive(LiveSession s);
  Future<void> stopLive(LiveSession s);

  /// The current picture, or null when the demo should draw its own.
  Future<Uint8List?> frame(LiveSession s);
}

class ApiRepository implements Repository {
  ApiRepository(this.base, this.token, {http.Client? client}) : _c = client ?? http.Client();
  final String base, token;
  final http.Client _c;

  Map<String, String> get _h => {'Authorization': 'Bearer $token', 'Content-Type': 'application/json'};
  Uri _u(String path, [Map<String, String>? q]) => Uri.parse('$base$path').replace(queryParameters: q);

  dynamic _ok(http.Response r) {
    if (r.statusCode >= 200 && r.statusCode < 300) return r.body.isEmpty ? null : jsonDecode(r.body);
    String msg = 'Something went wrong (${r.statusCode}).';
    try {
      msg = (jsonDecode(r.body)['error']['message'] as String?) ?? msg;
    } catch (_) {}
    throw ApiError(r.statusCode, msg);
  }

  @override
  Future<Health> health() async => Health.fromJson(_ok(await _c.get(_u('/v1/health-summary'), headers: _h)));

  @override
  Future<List<AlertItem>> alerts() async => [for (final j in _ok(await _c.get(_u('/v1/alerts'), headers: _h)) as List) AlertItem.fromJson(j)];

  @override
  Future<void> done(String id) async => _ok(await _c.post(_u('/v1/alerts/$id/acknowledge'), headers: _h));

  @override
  Future<void> snooze(String id, {int minutes = 60}) async => _ok(await _c.post(_u('/v1/alerts/$id/snooze'), headers: _h, body: jsonEncode({'minutes': minutes})));

  @override
  Future<List<CameraItem>> cameras() async => [
        for (final j in _ok(await _c.get(_u('/v1/cameras'), headers: _h)) as List)
          CameraItem(j['id'], j['name'], (j['recorder_name'] ?? '') as String, j['stream_state'] == 'streaming')
      ];

  @override
  Future<LiveSession> startLive(String cameraId, LiveMode mode) async {
    final j = _ok(await _c.post(_u('/v1/cameras/$cameraId/live'), headers: _h, body: jsonEncode({'mode': mode.name})));
    return LiveSession(j['id'], mode);
  }

  @override
  Future<void> extendLive(LiveSession s) async => _ok(await _c.post(_u('/v1/live/${s.id}/extend'), headers: _h));

  @override
  Future<void> stopLive(LiveSession s) async => _ok(await _c.delete(_u('/v1/live/${s.id}'), headers: _h));

  @override
  Future<Uint8List?> frame(LiveSession s) async {
    final r = await _c.get(_u('/v1/live/${s.id}/frame'), headers: _h);
    if (r.statusCode == 200) return r.bodyBytes;
    if (r.statusCode == 404) return null; // the picture is not ready yet
    _ok(r);
    return null;
  }
}

class ApiError implements Exception {
  ApiError(this.status, this.message);
  final int status;
  final String message;
  @override
  String toString() => message;
}

/// Demo data, like the clickable prototype. [problems] switches on a camera that is offline and a recorder that keeps dropping.
class MockRepository implements Repository {
  MockRepository({this.problems = false});
  bool problems;
  final Map<String, AlertState> _state = {};

  @override
  Future<Health> health() async => Health(
        ok: !problems,
        issues: problems ? const [Issue('camera_down', 'critical', 'Stockroom camera is not sending pictures'), Issue('recorder_unstable', 'warning', 'Godown recorder keeps dropping (6 times in the last hour)')] : const [],
        recordersOnline: 2,
        recordersTotal: 2,
        camerasWorking: problems ? 7 : 8,
        camerasTotal: 8,
        openAlerts: (await alerts()).where((a) => a.state == AlertState.open).length,
        sent24h: 12,
        failed24h: 0,
        dataSecondsAgo: problems ? 190 : 8,
      );

  @override
  Future<List<AlertItem>> alerts() async {
    final all = [
      if (problems) AlertItem(id: 'p1', title: 'Stockroom camera is offline', body: 'No picture for 14 minutes. Check the power and the network cable.', rule: 'camera_offline', where: 'Stockroom', when: '14 min ago'),
      if (problems) AlertItem(id: 'p2', title: 'Godown recorder keeps dropping', body: 'The link went up and down 6 times. This is usually the internet at the godown.', rule: 'connection_unstable', where: 'Godown', when: '1 hour'),
      AlertItem(id: 'a1', title: '9 people waiting for 6 minutes', body: 'More than 6 people have waited over 5 minutes. A second counter may help.', rule: 'queue_long', where: 'Billing queue', when: '10:42 am', smart: true),
      AlertItem(id: 'a2', title: 'Possible phone use at the counter', body: 'A phone may have been in use in the marked area for over 30 seconds.', rule: 'phone_use', where: 'Cash counter', when: '9:15 am', smart: true),
    ];
    for (final a in all) {
      a.state = _state[a.id] ?? AlertState.open;
    }
    return all;
  }

  @override
  Future<void> done(String id) async => _state[id] = AlertState.done;
  @override
  Future<void> snooze(String id, {int minutes = 60}) async => _state[id] = AlertState.snoozed;

  @override
  Future<List<CameraItem>> cameras() async => [
        const CameraItem('c1', 'Front door', 'Store recorder', true),
        const CameraItem('c2', 'Cash counter', 'Store recorder', true),
        const CameraItem('c3', 'Aisle 1', 'Store recorder', true),
        const CameraItem('c4', 'Aisle 2', 'Store recorder', true),
        const CameraItem('c5', 'Back door', 'Store recorder', true),
        const CameraItem('c6', 'Parking', 'Store recorder', true),
        CameraItem('c7', 'Stockroom', 'Godown recorder', !problems),
        const CameraItem('c8', 'Billing queue', 'Godown recorder', true),
      ];

  @override
  Future<LiveSession> startLive(String cameraId, LiveMode mode) async => LiveSession('demo-$cameraId', mode);
  @override
  Future<void> extendLive(LiveSession s) async {}
  @override
  Future<void> stopLive(LiveSession s) async {}
  @override
  Future<Uint8List?> frame(LiveSession s) async => null;
}
